//! Native extension module behind the `iec61850` Python package.
//!
//! Exposes the IEC 61850 MMS client and server of `iec61850-rust` to Python:
//! the association, the typed read and write surface, data sets, report and
//! log control blocks, control objects, setting groups, SCL parsing, TLS, and
//! an SNTP client. Every coroutine returned here resolves on the tokio runtime
//! that `pyo3-async-runtimes` drives, so a caller must have a running asyncio
//! loop when it creates one. Doc comments on `#[pyclass]` and `#[pymethods]`
//! items become Python `__doc__` strings and ship inside the compiled module.

use std::collections::HashMap;
use std::future::Future;
use std::net::SocketAddr;
use std::pin::Pin;
use std::sync::{Arc, RwLock as StdRwLock};
use std::time::Duration;

use iec61850_client::control::{
    ControlAddCause as RustControlAddCause, ControlModel as RustControlModel,
    ControlObjectClient as RustControlObjectClient, ControlOutcome as RustControlOutcome,
    OriginValue as RustOriginValue, SboClass as RustSboClass,
};
use iec61850_client::{
    AcsiClass as RustAcsiClass, ClientError, ClientJournalEntry as RustJournalEntry,
    ClientJournalEntryId as RustJournalEntryId, ClientReport as RustClientReport,
    DataSetMember as RustDataSetMember, DeviceModel as RustDeviceModel,
    IedConnection as RustIedConnection, RcbHandle as RustRcbHandle,
    RcbWriteMask as RustRcbWriteMask, ReportOptFlds as RustReportOptFlds,
    TriggerOptions as RustTriggerOptions,
};
use iec61850_mms::mms::pdu::common::DataAccessError as RustDataAccessError;
use iec61850_mms::TypeSpecification as RustTypeSpec;
use iec61850_model::builder::{
    DataObjectBuilder as RustDoBuilder, IedModelBuilder as RustIedModelBuilder,
    LogicalDeviceBuilder as RustLdBuilder, LogicalNodeBuilder as RustLnBuilder,
};
use iec61850_model::cb::{
    LogControlBlock as RustModelLcb, OptFlds as RustModelOptFlds,
    ReportControlBlock as RustModelRcb, SettingGroupControlBlock as RustModelSgcb,
};
use iec61850_model::tree::{
    DataAttribute as RustDataAttribute, DataSet as RustModelDataSet,
    DataSetEntry as RustModelDataSetEntry, DoChild as RustDoChild, IedModel as RustIedModel,
};
use iec61850_model::types::{
    DataAttributeType as RustDataAttributeType, TrgOps as RustModelTrgOps,
};
use iec61850_model::value::MmsValue as RustMmsValue;
use iec61850_model::FC as RustFC;
use iec61850_scl::raw::{
    DataTypeTemplates as RustDtt, GseControlType as RustGseControlType, OptionFieldsBits,
    RawAccessPoint, RawBda, RawDaDef, RawDaType, RawDai, RawDataInstance, RawDataSet, RawDoDef,
    RawDoType, RawDoi, RawEnumType, RawFcda, RawGseControl, RawIed, RawLNodeType, RawLogControl,
    RawLogicalDevice, RawLogicalNode, RawReportControlBlock, RawSampledValueControl, RawSdi,
    RawSdoDef, RawServer, RawSettingControl, RawVal, SampledValueSmpMod as RustSmpMod, SmvOptsBits,
    TriggerOptionsBits,
};
use iec61850_scl::{ResolvedScl as RustResolvedScl, SclParseError as RustSclParseError};
use iec61850_server::config::IedServerConfig as RustIedServerConfig;
use iec61850_server::connection::ConnectionId as RustConnectionId;
use iec61850_server::control::handler::{
    OperateFuture as RustOperateFuture, WaitForExecFuture as RustWaitForExecFuture,
};
use iec61850_server::control::{
    CheckHandler as RustCheckHandler, ControlAction as RustControlAction,
    ControlAddCause as RustServerControlAddCause, ControlHandler as RustControlHandler,
    ControlModel as RustServerControlModel, ControlObject as RustControlObject,
    ControlObjectConfig as RustControlObjectConfig, ControlObjectEntry as RustControlObjectEntry,
    SboClass as RustServerSboClass, WaitForExecutionHandler as RustWaitHandler,
};
use iec61850_server::error::ServerError as RustServerError;
use iec61850_server::handler::{
    AttributeAccessHandler as RustWriteHandler, ReadContext as RustReadContext,
    ReadHandler as RustReadHandler, ReadOutcome as RustReadOutcome,
    WriteContext as RustWriteContext, WriteOutcome as RustWriteOutcome,
};
use iec61850_server::lifecycle::ServerHandle as RustServerHandle;
use iec61850_server::logging::{
    InMemoryLogStorage as RustInMemoryLogStorage, LogControl as RustLogControl,
    LogControlBlock as RustLogControlBlock, LogStorage as RustLogStorage,
};
use iec61850_server::reporting::{
    Brcb as RustBrcb, BufferedReportControl as RustBufferedReportControl, Dataset as RustDataset,
    DatasetEntry as RustDatasetEntry, OptFlds as RustServerOptFlds, Rcb as RustRcb,
    ReportControl as RustReportControl, TriggerOptions as RustServerTriggerOptions,
};
use iec61850_server::setting_groups::{
    SettingGroupHandler as RustSettingGroupHandler, SgcbSnapshot as RustSgcbSnapshot,
};
use iec61850_server::{DataModelGuard as RustDataModelGuard, IedServer as RustIedServer};
use iec61850_sntp::{SntpClient as RustSntpClient, SntpError as RustSntpError};
use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyKeyError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyString, PyType};
use pyo3_async_runtimes::tokio::future_into_py;
use pyo3_async_runtimes::TaskLocals;
use tokio::sync::Mutex as AsyncMutex;

create_exception!(
    _native,
    IedError,
    PyException,
    "Base error for the iec61850 package."
);
create_exception!(
    _native,
    IedConnectionError,
    IedError,
    "TCP / OSI stack connection failure."
);
create_exception!(
    _native,
    IedTimeoutError,
    IedError,
    "Operation exceeded its deadline."
);
create_exception!(
    _native,
    IedDataAccessError,
    IedError,
    "Server returned a DataAccess service error."
);
create_exception!(
    _native,
    IedServiceError,
    IedError,
    "Generic MMS service-layer error."
);
create_exception!(
    _native,
    IedControlError,
    IedError,
    "Control select/operate/cancel rejected by server."
);
create_exception!(
    _native,
    SclError,
    IedError,
    "Failure parsing or resolving an SCL / ICD / CID document."
);
create_exception!(
    _native,
    IedServerError,
    IedError,
    "Failure starting, configuring, or running a hosted IED server."
);

/// Context for mapping `ClientError` → Python exception.
///
/// `connect()` failures are categorically `IedConnectionError` regardless of
/// the underlying variant (TCP refused / TLS handshake / ACSE reject all
/// surface as `ClientError::Mms(_)` in this version, and "MMS service error"
/// is a misleading classification at connect time).
#[derive(Copy, Clone)]
enum ErrCtx {
    Connect,
    Service,
}

fn map_client_error(err: ClientError, ctx: ErrCtx) -> PyErr {
    match (err, ctx) {
        (ClientError::NotConnected, _) => {
            IedConnectionError::new_err("IedConnection is not connected")
        }
        (err, ErrCtx::Connect) => IedConnectionError::new_err(err.to_string()),
        (err @ ClientError::TypeMismatch { .. }, _) => IedDataAccessError::new_err(err.to_string()),
        (err @ ClientError::UnexpectedValueType { .. }, _) => {
            IedDataAccessError::new_err(err.to_string())
        }
        (err @ ClientError::InvalidBinaryTimeLen { .. }, _) => {
            IedDataAccessError::new_err(err.to_string())
        }
        (err @ ClientError::InvalidArgument(_), _) => PyValueError::new_err(err.to_string()),
        (err @ ClientError::InvalidRptId { .. }, _) => PyValueError::new_err(err.to_string()),
        (err @ ClientError::AlreadyRegistered(_), _) => PyValueError::new_err(err.to_string()),
        (err @ ClientError::NotFound(_), _) => PyValueError::new_err(err.to_string()),
        (err @ ClientError::ParseFailed(_), _) => IedDataAccessError::new_err(err.to_string()),
        (err @ ClientError::DataAccessError(_), _) => IedDataAccessError::new_err(err.to_string()),
        (err @ ClientError::Mms(_), _) => IedServiceError::new_err(err.to_string()),
    }
}

fn parse_fc(token: &str) -> PyResult<RustFC> {
    RustFC::parse(token)
        .map_err(|e| PyValueError::new_err(format!("invalid FC token '{token}': {e:?}")))
}

/// Map the Python `AcsiClass` StrEnum value to the native ACSI class.
///
/// `"CO"` is rejected — control objects are queried via dedicated client
/// methods rather than the directory service.
fn parse_acsi_class(token: &str) -> PyResult<RustAcsiClass> {
    match token {
        "DO" => Ok(RustAcsiClass::DataObject),
        "DS" => Ok(RustAcsiClass::DataSet),
        "BR" => Ok(RustAcsiClass::Brcb),
        "RP" => Ok(RustAcsiClass::Urcb),
        "GO" => Ok(RustAcsiClass::GoCb),
        "SG" => Ok(RustAcsiClass::Sgcb),
        "LG" => Ok(RustAcsiClass::Log),
        "CO" => Err(PyValueError::new_err(
            "AcsiClass.CONTROL is not valid for directory queries; \
             use IedConnection.create_control_object() instead"
                .to_string(),
        )),
        other => Err(PyValueError::new_err(format!(
            "invalid ACSI class token '{other}'"
        ))),
    }
}

fn parse_addr(addr: &str) -> PyResult<(String, u16)> {
    let (host, port_str) = addr
        .rsplit_once(':')
        .ok_or_else(|| PyValueError::new_err(format!("address '{addr}' must be 'host:port'")))?;
    if host.is_empty() {
        return Err(PyValueError::new_err(format!(
            "host portion of '{addr}' is empty"
        )));
    }
    let port: u16 = port_str
        .parse()
        .map_err(|_| PyValueError::new_err(format!("port '{port_str}' is not a valid u16")))?;
    Ok((host.to_string(), port))
}

/// Parse a Python ``TlsVersion`` token into the native enum.
fn parse_tls_version(token: &str) -> PyResult<iec61850_tls::TlsVersion> {
    match token {
        "tls1.2" => Ok(iec61850_tls::TlsVersion::Tls12),
        "tls1.3" => Ok(iec61850_tls::TlsVersion::Tls13),
        other => Err(PyValueError::new_err(format!(
            "invalid TLS version '{other}'; expected tls1.2 or tls1.3"
        ))),
    }
}

/// Build a `TlsConnector` from a `TlsConfig` payload.
///
/// Defaults (matching the strict IEC 62351-3 profile): TLS 1.2-1.3, IEC
/// 62351-3 cipher whitelist, chain validation on, time validation on, SNI
/// verification on, allow-only-known peers off.
#[allow(clippy::too_many_arguments)]
fn build_tls_connector(
    ca_pem: &[u8],
    client_cert_pem: Option<&[u8]>,
    client_key_pem: Option<&[u8]>,
    verify_hostname: bool,
    allow_only_known_peers: bool,
    known_peer_pems: &[Vec<u8>],
    chain_validation: bool,
    time_validation: bool,
    crl_pems: &[Vec<u8>],
    min_version: iec61850_tls::TlsVersion,
    max_version: iec61850_tls::TlsVersion,
) -> PyResult<iec61850_tls::TlsConnector> {
    if client_cert_pem.is_some() ^ client_key_pem.is_some() {
        return Err(PyValueError::new_err(
            "client_cert_pem and client_key_pem must be provided together",
        ));
    }
    let mut builder = iec61850_tls::TlsConfigBuilder::new()
        .add_ca_pem(ca_pem)
        .map_err(|e| PyValueError::new_err(format!("load CA PEM: {e}")))?;
    if let (Some(cert), Some(key)) = (client_cert_pem, client_key_pem) {
        builder = builder
            .with_cert_pem(cert, key)
            .map_err(|e| PyValueError::new_err(format!("load client cert/key: {e}")))?;
    }
    if !verify_hostname {
        builder = builder.with_dangerous_no_sni_verify();
    }
    for pem in known_peer_pems {
        builder = builder
            .add_known_peer_pem(pem)
            .map_err(|e| PyValueError::new_err(format!("load known peer PEM: {e}")))?;
    }
    builder = builder
        .allow_only_known_peers(allow_only_known_peers)
        .chain_validation(chain_validation)
        .time_validation(time_validation)
        .min_version(min_version)
        .max_version(max_version);
    for pem in crl_pems {
        builder = builder
            .add_crl_pem(pem)
            .map_err(|e| PyValueError::new_err(format!("load CRL PEM: {e}")))?;
    }
    let cfg = builder
        .build_client()
        .map_err(|e| PyValueError::new_err(format!("build TLS client config: {e}")))?;
    Ok(iec61850_tls::TlsConnector::new(cfg))
}

/// Build a server-side `TlsAcceptor` from queued `with_tls` parameters.
///
/// Server requires a leaf cert chain + private key; client-auth knobs are
/// optional and default to the strict IEC 62351-3 profile (TLS 1.2-1.3,
/// cipher whitelist, chain + time validation on, no client-cert pinning).
fn build_tls_acceptor(pt: &PendingTls) -> PyResult<iec61850_tls::TlsAcceptor> {
    let mut builder = iec61850_tls::TlsConfigBuilder::new()
        .with_cert_pem(&pt.server_cert_pem, &pt.server_key_pem)
        .map_err(|e| PyValueError::new_err(format!("load server cert/key: {e}")))?;
    if let Some(ca) = &pt.client_ca_pem {
        builder = builder
            .add_ca_pem(ca)
            .map_err(|e| PyValueError::new_err(format!("load client CA PEM: {e}")))?;
    }
    for pem in &pt.known_peer_pems {
        builder = builder
            .add_known_peer_pem(pem)
            .map_err(|e| PyValueError::new_err(format!("load known peer PEM: {e}")))?;
    }
    builder = builder
        .allow_only_known_peers(pt.allow_only_known_peers)
        .chain_validation(pt.chain_validation)
        .time_validation(pt.time_validation)
        .session_resumption(pt.session_resumption)
        .min_version(pt.min_version)
        .max_version(pt.max_version);
    for pem in &pt.crl_pems {
        builder = builder
            .add_crl_pem(pem)
            .map_err(|e| PyValueError::new_err(format!("load CRL PEM: {e}")))?;
    }
    let cfg = builder
        .build_server()
        .map_err(|e| PyValueError::new_err(format!("build TLS server config: {e}")))?;
    Ok(iec61850_tls::TlsAcceptor::new(cfg))
}

/// Build an `IedConnection` whose underlying MMS client carries the requested
/// per-request tuning (request timeout, max outstanding invocations, locally
/// declared max PDU size). `None` leaves the corresponding default in place.
fn build_ied_connection(
    request_timeout_ms: Option<u64>,
    max_outstanding: Option<u32>,
    local_max_pdu_size: Option<u32>,
) -> RustIedConnection {
    let mut builder = iec61850_mms::MmsClientBuilder::new();
    if let Some(t) = request_timeout_ms {
        builder = builder.request_timeout_ms(t);
    }
    if let Some(n) = max_outstanding {
        builder = builder.max_outstanding(n);
    }
    if let Some(s) = local_max_pdu_size {
        builder = builder.local_max_pdu_size(s);
    }
    RustIedConnection::with_mms_client(builder.build())
}

/// Python-side handle for an IEC 61850 MMS client connection.
///
/// Held in an `Arc` so the same underlying client can be shared across the
/// `connect` future and any later read/write futures bridged through the async
/// runtime. `frozen` means no `&mut self` methods — all mutation already lives
/// behind internal mutexes.
#[pyclass(name = "IedConnection", module = "iec61850._native", frozen)]
struct PyIedConnection {
    inner: Arc<RustIedConnection>,
}

#[pymethods]
impl PyIedConnection {
    /// Construct + connect in one step. `addr` is `"host:port"`.
    ///
    /// `timeout_ms` wraps the entire connect handshake. The optional
    /// `request_timeout_ms` / `max_outstanding` / `local_max_pdu_size` tune
    /// the underlying MMS client for after the connection is established.
    #[classmethod]
    #[pyo3(signature = (
        addr,
        *,
        timeout_ms = 5000,
        request_timeout_ms = None,
        max_outstanding = None,
        local_max_pdu_size = None,
    ))]
    fn connect<'py>(
        _cls: &Bound<'py, PyType>,
        py: Python<'py>,
        addr: String,
        timeout_ms: u64,
        request_timeout_ms: Option<u64>,
        max_outstanding: Option<u32>,
        local_max_pdu_size: Option<u32>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let (host, port) = parse_addr(&addr)?;
        future_into_py(py, async move {
            let conn =
                build_ied_connection(request_timeout_ms, max_outstanding, local_max_pdu_size);
            let deadline = std::time::Duration::from_millis(timeout_ms);
            tokio::time::timeout(deadline, conn.connect(&host, port))
                .await
                .map_err(|_| {
                    IedTimeoutError::new_err(format!(
                        "connect to {host}:{port} timed out after {timeout_ms} ms"
                    ))
                })?
                .map_err(|e| map_client_error(e, ErrCtx::Connect))?;
            Ok(PyIedConnection {
                inner: Arc::new(conn),
            })
        })
    }

    /// Construct + TLS handshake + MMS Initiate. `addr` is `"host:port"`;
    /// `server_name` is the SNI / certificate validation name. `ca_pem` is
    /// required. `client_cert_pem` + `client_key_pem` enable mutual TLS and
    /// must be supplied together. `verify_hostname=False` skips SNI / SAN
    /// hostname matching. ``known_peer_pems`` + ``allow_only_known_peers``
    /// pin specific server certificates (IEC 62351-3 known-peer profile).
    /// ``min_tls_version`` / ``max_tls_version`` accept ``"tls1.2"`` or
    /// ``"tls1.3"``.
    #[classmethod]
    #[pyo3(signature = (
        addr,
        *,
        server_name,
        ca_pem,
        client_cert_pem = None,
        client_key_pem = None,
        verify_hostname = true,
        allow_only_known_peers = false,
        known_peer_pems = Vec::new(),
        chain_validation = true,
        time_validation = true,
        crl_pems = Vec::new(),
        min_tls_version = "tls1.2".to_string(),
        max_tls_version = "tls1.3".to_string(),
        timeout_ms = 5000,
        request_timeout_ms = None,
        max_outstanding = None,
        local_max_pdu_size = None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn connect_tls<'py>(
        _cls: &Bound<'py, PyType>,
        py: Python<'py>,
        addr: String,
        server_name: String,
        ca_pem: Vec<u8>,
        client_cert_pem: Option<Vec<u8>>,
        client_key_pem: Option<Vec<u8>>,
        verify_hostname: bool,
        allow_only_known_peers: bool,
        known_peer_pems: Vec<Vec<u8>>,
        chain_validation: bool,
        time_validation: bool,
        crl_pems: Vec<Vec<u8>>,
        min_tls_version: String,
        max_tls_version: String,
        timeout_ms: u64,
        request_timeout_ms: Option<u64>,
        max_outstanding: Option<u32>,
        local_max_pdu_size: Option<u32>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let (host, port) = parse_addr(&addr)?;
        let sni = rustls::pki_types::ServerName::try_from(server_name.clone()).map_err(|e| {
            PyValueError::new_err(format!("invalid server_name '{server_name}': {e}"))
        })?;
        let min_version = parse_tls_version(&min_tls_version)?;
        let max_version = parse_tls_version(&max_tls_version)?;
        let connector = build_tls_connector(
            &ca_pem,
            client_cert_pem.as_deref(),
            client_key_pem.as_deref(),
            verify_hostname,
            allow_only_known_peers,
            &known_peer_pems,
            chain_validation,
            time_validation,
            &crl_pems,
            min_version,
            max_version,
        )?;
        future_into_py(py, async move {
            let deadline = Duration::from_millis(timeout_ms);
            let mut addrs = tokio::net::lookup_host((host.as_str(), port))
                .await
                .map_err(|e| IedConnectionError::new_err(format!("resolve {host}:{port}: {e}")))?;
            let socket_addr = addrs.next().ok_or_else(|| {
                IedConnectionError::new_err(format!("no address for {host}:{port}"))
            })?;
            let conn =
                build_ied_connection(request_timeout_ms, max_outstanding, local_max_pdu_size);
            tokio::time::timeout(deadline, conn.connect_tls(socket_addr, &connector, sni))
                .await
                .map_err(|_| {
                    IedTimeoutError::new_err(format!(
                        "TLS connect to {host}:{port} timed out after {timeout_ms} ms"
                    ))
                })?
                .map_err(|e| map_client_error(e, ErrCtx::Connect))?;
            Ok(PyIedConnection {
                inner: Arc::new(conn),
            })
        })
    }

    /// Graceful MMS Conclude + TCP close.
    fn disconnect<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            conn.disconnect()
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Rude close — skip the MMS Conclude exchange and drop the TCP socket.
    ///
    /// Use when graceful disconnect cannot be relied on: peer is unresponsive,
    /// the protocol layer is wedged, or the application detected an abort
    /// condition. Always succeeds: the internal state is cleared regardless
    /// of the socket state.
    fn abort<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            conn.abort()
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Whether the connection is currently established. Synchronous getter
    /// — cheap atomic load on the Rust side.
    #[getter]
    fn is_connected(&self) -> bool {
        self.inner.is_connected()
    }

    /// Read a `BOOLEAN` DA.
    fn read_bool<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.read_boolean(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Read an `INTEGER` DA as `i32`. Also accepts server `UNSIGNED` if it
    /// fits `i32`.
    fn read_int32<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.read_int32(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Read an `UNSIGNED` DA as `u32`. Also accepts non-negative `INTEGER`.
    fn read_uint32<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.read_uint32(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Read an `INTEGER` DA as `i64`. Also accepts server `UNSIGNED` if it
    /// fits `i64`.
    fn read_int64<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.read_int64(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Read a `FLOAT32` DA as Python ``float``. Strict — Float64 is rejected
    /// to avoid silent precision loss.
    fn read_float<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.read_float(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Read a `FLOAT64` DA as Python ``float``.
    fn read_float64<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.read_float64(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Read a `VISIBLE_STRING` or `MMS_STRING` DA.
    fn read_string<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.read_string(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Read a `UTC_TIME` DA as raw 8-byte buffer (IEC 61850-8-1 Annex E).
    ///
    /// Wire format: bytes 0..4 = `SecondSinceEpoch` (big-endian u32),
    /// bytes 4..7 = 24-bit big-endian fraction (`frac / 2^24` seconds),
    /// byte 7 = `TimeQuality`. Python wrapper decodes to `datetime`.
    fn read_timestamp<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            let arr = conn
                .read_timestamp(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(arr.to_vec())
        })
    }

    /// Read a `BIT_STRING(13)` Quality DA, returning the packed 16-bit
    /// representation. Python wrapper decodes to the ``Quality`` dataclass.
    fn read_quality_bits<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            let q = conn
                .read_quality(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(q.0)
        })
    }

    /// Write a `BOOLEAN` DA.
    fn write_bool<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
        value: bool,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.write_boolean(&reference, fc, value)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(())
        })
    }

    /// Write an `INTEGER` DA from an `i32`.
    fn write_int32<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
        value: i32,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.write_int32(&reference, fc, value)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(())
        })
    }

    /// Write an `UNSIGNED` DA from a `u32`.
    fn write_uint32<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
        value: u32,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.write_uint32(&reference, fc, value)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(())
        })
    }

    /// Write a `FLOAT32` DA.
    fn write_float<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
        value: f32,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.write_float(&reference, fc, value)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(())
        })
    }

    /// Write an `OCTET_STRING` DA from Python ``bytes``.
    fn write_octet_string<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
        value: Vec<u8>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.write_octet_string(&reference, fc, value)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(())
        })
    }

    /// Write a `VISIBLE_STRING` DA.
    fn write_visible_string<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
        value: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            conn.write_visible_string(&reference, fc, value)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(())
        })
    }

    /// Read an object and return the closest native Python representation.
    ///
    /// Scalars surface as native ``bool`` / ``int`` / ``float`` / ``str``.
    /// ``OCTET_STRING`` / ``BIT_STRING`` / ``UTC_TIME`` / ``BINARY_TIME``
    /// surface as ``bytes``. ``ARRAY`` and ``STRUCTURE`` surface as ``list``.
    /// Array-element and sub-component reads use the same encoding as
    /// ``read_object`` of the underlying library: ``"...DO(idx)"`` or
    /// ``"...DO(idx).sub"`` in the ``reference`` string.
    fn read_object<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            let value = conn
                .read_object(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Python::attach(|py| mms_value_to_pyobject(py, &value))
        })
    }

    /// Write a Python value as an object.
    ///
    /// ``bool`` → Boolean, ``int`` → Integer, ``float`` → Float32, ``str`` →
    /// VisibleString, ``bytes`` → OctetString, ``list`` → Array (recursively
    /// converted with the same rules). For wire-level types that need
    /// explicit metadata (BitString padding) use the typed write methods.
    fn write_object<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
        value: Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        let mms = py_value_to_mms_value_generic(&value)?;
        future_into_py(py, async move {
            conn.write_object(&reference, fc, mms)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(())
        })
    }

    /// List the logical device names exposed by the server.
    fn get_server_directory<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            conn.get_server_directory(false)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// List the logical node names inside the given logical device.
    fn get_logical_device_directory<'py>(
        &self,
        py: Python<'py>,
        ld_name: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            conn.get_logical_device_directory(&ld_name)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// List names of objects of the given ACSI class inside a logical node.
    ///
    /// `ln_ref` is `"<LD>/<LN>"`. `class_token` is the Python ``AcsiClass``
    /// string value (`"DO"` / `"DS"` / `"BR"` / `"RP"` / `"GO"` / `"SG"` /
    /// `"LG"`).
    fn get_logical_node_directory<'py>(
        &self,
        py: Python<'py>,
        ln_ref: String,
        class_token: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let class = parse_acsi_class(&class_token)?;
        future_into_py(py, async move {
            conn.get_logical_node_directory(&ln_ref, class)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// List the sub-element names one level under a data object.
    ///
    /// `data_ref` is `"<LD>/<LN>.<DO>[.<sub>]*"`. Names returned do not carry
    /// FC suffixes; sub-elements with the same name across different FCs are
    /// deduplicated.
    fn get_data_directory<'py>(
        &self,
        py: Python<'py>,
        data_ref: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            conn.get_data_directory(&data_ref)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Query the MMS `TypeSpecification` of one named variable.
    ///
    /// `reference` is `"<LD>/<LN>.<DO>[.<sub>]*"` (without array index).
    /// Returns a nested dict whose root carries a `"kind"` discriminator —
    /// scalar kinds add a few payload fields, `array` adds `element_count` +
    /// `element_type`, `structure` adds `components` (a list of
    /// `{"name", "type"}` entries).
    fn get_variable_specification<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        fc: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let fc = parse_fc(&fc)?;
        future_into_py(py, async move {
            let ts = conn
                .get_variable_specification(&reference, fc)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Python::attach(|py| Ok(type_spec_to_pydict(py, &ts)?.unbind()))
        })
    }

    /// Return the cached device-model snapshot — a list of logical devices,
    /// each carrying its MMS NamedVariable names. When the cache is empty
    /// (or `refresh=True`), the server is queried first.
    ///
    /// Shape: `{"logical_devices": [{"name": "...", "variables": [...]}, ...]}`.
    #[pyo3(signature = (refresh = false))]
    fn get_device_model<'py>(&self, py: Python<'py>, refresh: bool) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let model = if refresh {
                conn.get_device_model_from_server()
                    .await
                    .map_err(|e| map_client_error(e, ErrCtx::Service))?
            } else {
                match conn.cached_device_model().await {
                    Some(m) => m,
                    None => conn
                        .get_device_model_from_server()
                        .await
                        .map_err(|e| map_client_error(e, ErrCtx::Service))?,
                }
            };
            Python::attach(|py| Ok(device_model_to_pydict(py, &model)?.unbind()))
        })
    }

    /// Create a dynamic dataset on the server.
    ///
    /// `reference` is `"<LD>/<LN>.<dsName>"`.
    /// `members` is a sequence of `(reference, fc_token)` pairs where each
    /// member reference is `"<LD>/<LN>.<DO>[.<sub>]*"` and `fc_token` is the
    /// two-letter Functional Constraint (e.g. `"ST"`).
    fn create_data_set<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        members: Vec<(String, String)>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let mut rust_members: Vec<RustDataSetMember> = Vec::with_capacity(members.len());
        for (mem_ref, fc_token) in members {
            let fc = parse_fc(&fc_token)?;
            rust_members.push(RustDataSetMember::new(mem_ref, fc));
        }
        future_into_py(py, async move {
            conn.create_data_set(&reference, &rust_members)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(())
        })
    }

    /// Delete a dynamic dataset from the server.
    ///
    /// `reference` is `"<LD>/<LN>.<dsName>"`. Returns `True` when the server
    /// confirmed deletion (`numberDeleted >= 1`); `False` when the dataset
    /// was unknown or refused (static / mmsDeletable=false).
    fn delete_data_set<'py>(
        &self,
        py: Python<'py>,
        reference: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            conn.delete_data_set(&reference)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Read every value of a dataset (B2 — GetDataSetValues).
    ///
    /// Returns a list whose entries follow ``read_object`` conversion. If any
    /// entry's access fails on the server, raises ``IedDataAccessError`` with
    /// the entry index and the wire error code.
    fn get_data_set_values<'py>(
        &self,
        py: Python<'py>,
        reference: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let results = conn
                .get_data_set_values(&reference)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Python::attach(|py| {
                let list = PyList::empty(py);
                for (idx, entry) in results.iter().enumerate() {
                    match entry {
                        iec61850_mms::AccessResult::Success(data) => {
                            let value = iec61850_client::mms_compat::mms_data_to_mms_value(data);
                            list.append(mms_value_to_pyobject(py, &value)?)?;
                        }
                        iec61850_mms::AccessResult::Failure(err) => {
                            return Err(IedDataAccessError::new_err(format!(
                                "dataset entry {idx}: {err:?}"
                            )));
                        }
                    }
                }
                Ok(list.unbind())
            })
        })
    }

    /// Write every value of a dataset (B2 — SetDataSetValues).
    ///
    /// ``values`` must have the same length as the server-side dataset.
    /// Each value is converted with the same rules as ``write_object``. Any
    /// per-entry failure raises ``IedDataAccessError`` with the entry index.
    fn set_data_set_values<'py>(
        &self,
        py: Python<'py>,
        reference: String,
        values: Vec<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let mut mms_values: Vec<RustMmsValue> = Vec::with_capacity(values.len());
        for v in &values {
            mms_values.push(py_value_to_mms_value_generic(v)?);
        }
        future_into_py(py, async move {
            let outcomes = conn
                .set_data_set_values(&reference, mms_values)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            for (idx, outcome) in outcomes.iter().enumerate() {
                if let iec61850_mms::WriteOutcome::Failure(err) = outcome {
                    return Err(IedDataAccessError::new_err(format!(
                        "dataset entry {idx}: {err:?}"
                    )));
                }
            }
            Ok(())
        })
    }

    /// Read RCB settings from the server. Returns an opaque ``RcbHandle``.
    ///
    /// `rcb_ref` is the objectReference, e.g. `"<LD>/<LN>$RP$<rcbName>"`
    /// (URCB) or `"<LD>/<LN>$BR$<rcbName>"` (BRCB).
    fn get_rcb_values<'py>(&self, py: Python<'py>, rcb_ref: String) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let handle = conn
                .get_rcb_values(&rcb_ref)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(PyRcbHandle {
                inner: Arc::new(AsyncMutex::new(handle)),
            })
        })
    }

    /// Re-read the same RCB and update fields on the existing handle in place.
    fn refresh_rcb_values<'py>(
        &self,
        py: Python<'py>,
        rcb: &PyRcbHandle,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let handle = Arc::clone(&rcb.inner);
        future_into_py(py, async move {
            let mut guard = handle.lock().await;
            conn.refresh_rcb_values(&mut guard)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Write the masked fields of ``rcb`` back to the server.
    ///
    /// `mask_bits` is a raw `RcbWriteMask` bit-set (see
    /// `iec61850.RcbWriteMask`). Read-only fields (conf_rev / sq_num /
    /// owner / entry_time) are filtered server-side.
    fn set_rcb_values<'py>(
        &self,
        py: Python<'py>,
        rcb: &PyRcbHandle,
        mask_bits: u32,
        single_request: bool,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let handle = Arc::clone(&rcb.inner);
        let mask = RustRcbWriteMask::from_bits_truncate(mask_bits);
        future_into_py(py, async move {
            let guard = handle.lock().await;
            conn.set_rcb_values(&guard, mask, single_request)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Trigger a General Interrogation by writing ``<rcb_ref>$GI = true``.
    fn trigger_gi<'py>(&self, py: Python<'py>, rcb_ref: String) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            conn.trigger_grefcb(&rcb_ref)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Register a callback for a given RCB. The callback receives a single
    /// ``dict`` argument shaped as ``ClientReport.from_dict()`` expects on
    /// the Python side. Returns when the registration is acknowledged
    /// locally (no MMS round-trip).
    ///
    /// `rpt_id` overrides the lookup key; if ``None`` the registry uses
    /// ``rcb_ref.replace('.', '$')``.
    fn install_report_handler<'py>(
        &self,
        py: Python<'py>,
        rcb_ref: String,
        rpt_id: Option<String>,
        callback: Py<PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        let callback = Arc::new(callback);
        let report_cb: iec61850_client::ReportCallback = Arc::new(move |report| {
            let cb = Arc::clone(&callback);
            Python::attach(|py| {
                let dict = match client_report_to_pydict(py, &report) {
                    Ok(d) => d,
                    Err(e) => {
                        tracing::warn!(error = %e, "report could not be converted to a dict, callback skipped");
                        return;
                    }
                };
                if let Err(err) = cb.call1(py, (dict,)) {
                    err.print(py);
                }
            });
        });
        future_into_py(py, async move {
            conn.install_report_handler(rpt_id, &rcb_ref, None, report_cb)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Remove a previously installed report handler.
    fn uninstall_report_handler<'py>(
        &self,
        py: Python<'py>,
        rcb_ref: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            conn.uninstall_report_handler(&rcb_ref)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Caller-driven dispatch: pull pending InformationReports up to
    /// ``timeout_ms`` and dispatch them to installed handlers. Returns the
    /// number of dispatched URCB reports.
    fn poll_reports<'py>(&self, py: Python<'py>, timeout_ms: u64) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            conn.poll_reports(Duration::from_millis(timeout_ms))
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// Query journal entries by time range (IEC 61850-7-2 QueryLogByTime).
    ///
    /// `log_ref` is `"<domain>/<item>"` (e.g.
    /// `"DemoIEDLD0/LLN0$LG$evlog"`). Returns
    /// `{"entries": [...], "more_follows": bool}`. Each entry is a dict with
    /// `entry_id` (8-byte big-endian identifier), `time_ms`, and `variables`
    /// (list of `{"data_ref", "value", "reason_code"}`).
    fn query_journal_by_time<'py>(
        &self,
        py: Python<'py>,
        log_ref: String,
        start_ms: u64,
        end_ms: u64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let (entries, more_follows) = conn
                .query_journal_by_time(&log_ref, start_ms, end_ms)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Python::attach(|py| Ok(journal_result_to_pydict(py, &entries, more_follows)?.unbind()))
        })
    }

    /// Query journal entries strictly after a given entry (QueryLogAfterEntry).
    ///
    /// `entry_id` must be 8 bytes (big-endian wire form). The server applies
    /// both `starting_time_ms` and `entry_id` as filters — pass the time-stamp
    /// recorded against the last entry the caller already has.
    fn query_journal_after_entry<'py>(
        &self,
        py: Python<'py>,
        log_ref: String,
        starting_time_ms: u64,
        entry_id: Vec<u8>,
    ) -> PyResult<Bound<'py, PyAny>> {
        if entry_id.len() != 8 {
            return Err(PyValueError::new_err(format!(
                "entry_id must be exactly 8 bytes, got {}",
                entry_id.len()
            )));
        }
        let mut id_bytes = [0u8; 8];
        id_bytes.copy_from_slice(&entry_id);
        let id = RustJournalEntryId::from_bytes(id_bytes);
        let conn = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let (entries, more_follows) = conn
                .query_journal_after_entry(&log_ref, starting_time_ms, id)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Python::attach(|py| Ok(journal_result_to_pydict(py, &entries, more_follows)?.unbind()))
        })
    }

    /// Create a control handle for a single controllable DO.
    ///
    /// `object_ref` is `"<LD>/<LN>.<DO>"`. `ctl_model` is one of
    /// `"status-only"`, `"direct-normal"`, `"sbo-normal"`, `"direct-enhanced"`,
    /// `"sbo-enhanced"` (matching the wire `ctlModel` enum).
    #[pyo3(signature = (object_ref, ctl_model))]
    fn create_control_object(
        &self,
        object_ref: String,
        ctl_model: String,
    ) -> PyResult<PyControlObjectClient> {
        let model = parse_ctl_model(&ctl_model)?;
        let handle = self
            .inner
            .create_control_object(&object_ref, model)
            .map_err(|e| map_client_error(e, ErrCtx::Service))?;
        Ok(PyControlObjectClient {
            inner: Arc::new(AsyncMutex::new(handle)),
        })
    }
}

// ── PyControlObjectClient ────────────────────────────────────────────────────

/// Client handle for a single controllable DO. Built via
/// ``IedConnection.create_control_object()``.
#[pyclass(name = "ControlObjectClient", module = "iec61850._native", frozen)]
struct PyControlObjectClient {
    inner: Arc<AsyncMutex<RustControlObjectClient>>,
}

#[pymethods]
impl PyControlObjectClient {
    /// IEC-style object reference (`"<LD>/<LN>.<DO>"`).
    #[getter]
    fn object_reference(&self) -> String {
        self.inner.blocking_lock().object_ref().to_string()
    }

    /// MMS domain segment (`"<LD>"`).
    #[getter]
    fn domain(&self) -> String {
        self.inner.blocking_lock().domain().to_string()
    }

    /// Current control model as wire token.
    #[getter]
    fn ctl_model(&self) -> &'static str {
        ctl_model_token(self.inner.blocking_lock().ctl_model())
    }

    /// Override the origin (`orCat` + `orIdent`). The Python ``OriginValue``
    /// dataclass enforces the field-range invariants; values are taken on
    /// trust here.
    fn set_origin(&self, or_cat: i32, or_ident: Vec<u8>) {
        self.inner
            .blocking_lock()
            .set_origin(RustOriginValue { or_cat, or_ident });
    }

    /// Force `ctlNum` (mostly for sbo-enhanced where Oper must reuse the
    /// SBOw value).
    fn set_ctl_num(&self, ctl_num: u8) {
        self.inner.blocking_lock().set_ctl_num(ctl_num);
    }

    /// Set the `Test` flag for the next operate / select / cancel.
    fn set_test(&self, on: bool) {
        self.inner.blocking_lock().set_test(on);
    }

    /// Set the `synchroCheck` flag.
    fn set_synchro_check(&self, on: bool) {
        self.inner.blocking_lock().set_synchro_check(on);
    }

    /// Set the `interlockCheck` flag.
    fn set_interlock_check(&self, on: bool) {
        self.inner.blocking_lock().set_interlock_check(on);
    }

    /// Set the SBO class (`"operate-once"` or `"operate-many"`).
    fn set_sbo_class(&self, sbo_class: String) -> PyResult<()> {
        let cls = parse_sbo_class(&sbo_class)?;
        self.inner.blocking_lock().set_sbo_class(cls);
        Ok(())
    }

    /// SBO-normal select: Read `<LN>$CO$<DO>$SBO`. Returns ``True`` when the
    /// server accepted the selection.
    fn select<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let handle = Arc::clone(&self.inner);
        future_into_py(py, async move {
            let guard = handle.lock().await;
            guard
                .select()
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))
        })
    }

    /// SBO-enhanced select-with-value: Write `<LN>$CO$<DO>$SBOw`.
    ///
    /// Returns ``(success: bool, add_cause: str | None)``.
    fn select_with_value<'py>(
        &self,
        py: Python<'py>,
        value: Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = Arc::clone(&self.inner);
        let mms = py_value_to_mms_value(&value)?;
        future_into_py(py, async move {
            let guard = handle.lock().await;
            let outcome = guard
                .select_with_value(mms)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(outcome_to_pair(outcome))
        })
    }

    /// Operate: Write `<LN>$CO$<DO>$Oper`.
    ///
    /// In ``direct-enhanced`` / ``sbo-enhanced`` mode this also waits for
    /// `CommandTermination+/-`. Returns ``(success: bool, add_cause: str | None)``.
    fn operate<'py>(
        &self,
        py: Python<'py>,
        value: Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = Arc::clone(&self.inner);
        let mms = py_value_to_mms_value(&value)?;
        future_into_py(py, async move {
            let guard = handle.lock().await;
            let outcome = guard
                .operate(mms)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(outcome_to_pair(outcome))
        })
    }

    /// Cancel: Write `<LN>$CO$<DO>$Cancel`.
    fn cancel<'py>(
        &self,
        py: Python<'py>,
        value: Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = Arc::clone(&self.inner);
        let mms = py_value_to_mms_value(&value)?;
        future_into_py(py, async move {
            let guard = handle.lock().await;
            let outcome = guard
                .cancel(mms)
                .await
                .map_err(|e| map_client_error(e, ErrCtx::Service))?;
            Ok(outcome_to_pair(outcome))
        })
    }
}

fn parse_ctl_model(token: &str) -> PyResult<RustControlModel> {
    match token {
        "status-only" => Ok(RustControlModel::StatusOnly),
        "direct-normal" => Ok(RustControlModel::DirectNormal),
        "sbo-normal" => Ok(RustControlModel::SboNormal),
        "direct-enhanced" => Ok(RustControlModel::DirectEnhanced),
        "sbo-enhanced" => Ok(RustControlModel::SboEnhanced),
        other => Err(PyValueError::new_err(format!(
            "invalid control model '{other}'; expected one of \
             status-only / direct-normal / sbo-normal / direct-enhanced / sbo-enhanced"
        ))),
    }
}

fn ctl_model_token(m: RustControlModel) -> &'static str {
    match m {
        RustControlModel::StatusOnly => "status-only",
        RustControlModel::DirectNormal => "direct-normal",
        RustControlModel::SboNormal => "sbo-normal",
        RustControlModel::DirectEnhanced => "direct-enhanced",
        RustControlModel::SboEnhanced => "sbo-enhanced",
    }
}

fn parse_sbo_class(token: &str) -> PyResult<RustSboClass> {
    match token {
        "operate-once" => Ok(RustSboClass::OperateOnce),
        "operate-many" => Ok(RustSboClass::OperateMany),
        other => Err(PyValueError::new_err(format!(
            "invalid sbo class '{other}'; expected operate-once or operate-many"
        ))),
    }
}

fn add_cause_token(c: RustControlAddCause) -> &'static str {
    match c {
        RustControlAddCause::Unknown => "unknown",
        RustControlAddCause::NotSupported => "not-supported",
        RustControlAddCause::BlockedBySwitchingHierarchy => "blocked-by-switching-hierarchy",
        RustControlAddCause::SelectFailed => "select-failed",
        RustControlAddCause::InvalidPosition => "invalid-position",
        RustControlAddCause::PositionReached => "position-reached",
        RustControlAddCause::ParameterChangeInExecution => "parameter-change-in-execution",
        RustControlAddCause::StepLimit => "step-limit",
        RustControlAddCause::BlockedByMode => "blocked-by-mode",
        RustControlAddCause::BlockedByProcess => "blocked-by-process",
        RustControlAddCause::BlockedByInterlocking => "blocked-by-interlocking",
        RustControlAddCause::BlockedBySynchroCheck => "blocked-by-synchrocheck",
        RustControlAddCause::CommandAlreadyInExecution => "command-already-in-execution",
        RustControlAddCause::BlockedByHealth => "blocked-by-health",
        RustControlAddCause::OneOfNControl => "one-of-n-control",
        RustControlAddCause::AbortionByCancel => "abortion-by-cancel",
        RustControlAddCause::TimeLimitOver => "time-limit-over",
        RustControlAddCause::AbortionByTrip => "abortion-by-trip",
        RustControlAddCause::ObjectNotSelected => "object-not-selected",
        RustControlAddCause::ObjectAlreadySelected => "object-already-selected",
        RustControlAddCause::NoAccessAuthority => "no-access-authority",
        RustControlAddCause::EndedWithOvershoot => "ended-with-overshoot",
        RustControlAddCause::AbortionDueToDeviation => "abortion-due-to-deviation",
        RustControlAddCause::AbortionByCommunicationLoss => "abortion-by-communication-loss",
        RustControlAddCause::AbortionByCommand => "abortion-by-command",
        RustControlAddCause::None => "none",
        RustControlAddCause::InconsistentParameters => "inconsistent-parameters",
        RustControlAddCause::LockedByOtherClient => "locked-by-other-client",
    }
}

fn outcome_to_pair(outcome: RustControlOutcome) -> (bool, Option<&'static str>) {
    match outcome {
        RustControlOutcome::Success => (true, None),
        RustControlOutcome::Failure(cause) => (false, Some(add_cause_token(cause))),
    }
}

/// Convert a Python value to a generic ``MmsValue`` for ``write_object``.
///
/// ``bool`` → Boolean, ``float`` → Float32, ``int`` → Integer, ``str`` →
/// VisibleString, ``bytes`` → OctetString, ``list`` → Array (recursively).
fn py_value_to_mms_value_generic(value: &Bound<'_, PyAny>) -> PyResult<RustMmsValue> {
    if let Ok(b) = value.extract::<bool>() {
        return Ok(RustMmsValue::Boolean(b));
    }
    if value.is_instance_of::<pyo3::types::PyFloat>() {
        return Ok(RustMmsValue::Float32(value.extract::<f32>()?));
    }
    if let Ok(i) = value.extract::<i64>() {
        return Ok(RustMmsValue::Integer(i));
    }
    if let Ok(s) = value.extract::<String>() {
        return Ok(RustMmsValue::VisibleString(s));
    }
    if let Ok(bytes) = value.extract::<Vec<u8>>() {
        // Bytes-like accepts both `bytes` and `bytearray`; reject plain `list`
        // here so it goes to the list branch instead.
        if value.is_instance_of::<pyo3::types::PyBytes>()
            || value.is_instance_of::<pyo3::types::PyByteArray>()
        {
            return Ok(RustMmsValue::OctetString(bytes));
        }
    }
    if let Ok(list) = value.cast::<PyList>() {
        let mut items = Vec::with_capacity(list.len());
        for elem in list.iter() {
            items.push(py_value_to_mms_value_generic(&elem)?);
        }
        return Ok(RustMmsValue::Array(items));
    }
    Err(PyValueError::new_err(
        "write value must be bool / int / float / str / bytes / list",
    ))
}

/// Convert a Python scalar to the closest ``MmsValue`` for control payloads.
///
/// Supports ``bool`` (SPC), ``int`` (DPC / ENC / INC), and ``float`` (APC).
/// Other CDC payloads are out of scope for this release.
fn py_value_to_mms_value(value: &Bound<'_, PyAny>) -> PyResult<RustMmsValue> {
    // Order matters: `bool` is a subclass of `int` in Python, so this branch
    // must run before the integer fallback. `float` likewise needs an explicit
    // type check — `extract::<f32>()` would otherwise accept an `int`.
    if let Ok(b) = value.extract::<bool>() {
        return Ok(RustMmsValue::Boolean(b));
    }
    if value.is_instance_of::<pyo3::types::PyFloat>() {
        return Ok(RustMmsValue::Float32(value.extract::<f32>()?));
    }
    if let Ok(i) = value.extract::<i64>() {
        return Ok(RustMmsValue::Integer(i));
    }
    Err(PyValueError::new_err(
        "control value must be bool / int / float",
    ))
}

// ── PyRcbHandle ──────────────────────────────────────────────────────────────

/// Opaque mirror of a server-side Report Control Block.
///
/// Mutate via setters, then push with ``IedConnection.set_rcb_values()``.
/// Read-only fields (``conf_rev`` / ``sq_num`` / ``owner`` / ``time_of_entry_ms``)
/// expose getters only.
#[pyclass(name = "RcbHandle", module = "iec61850._native", frozen)]
struct PyRcbHandle {
    inner: Arc<AsyncMutex<RustRcbHandle>>,
}

#[pymethods]
impl PyRcbHandle {
    #[getter]
    fn object_reference(&self) -> String {
        self.inner.blocking_lock().object_reference().to_string()
    }

    #[getter]
    fn is_buffered(&self) -> bool {
        self.inner.blocking_lock().is_buffered()
    }

    #[getter]
    fn rpt_id(&self) -> Option<String> {
        self.inner.blocking_lock().rpt_id().map(str::to_string)
    }

    #[setter]
    fn set_rpt_id(&self, value: String) {
        self.inner.blocking_lock().set_rpt_id(&value);
    }

    #[getter]
    fn rpt_ena(&self) -> bool {
        self.inner.blocking_lock().rpt_ena()
    }

    #[setter]
    fn set_rpt_ena(&self, value: bool) {
        self.inner.blocking_lock().set_rpt_ena(value);
    }

    #[getter]
    fn resv(&self) -> bool {
        self.inner.blocking_lock().resv()
    }

    #[setter]
    fn set_resv(&self, value: bool) {
        self.inner.blocking_lock().set_resv(value);
    }

    #[getter]
    fn data_set_reference(&self) -> Option<String> {
        self.inner
            .blocking_lock()
            .data_set_reference()
            .map(str::to_string)
    }

    #[setter]
    fn set_data_set_reference(&self, value: String) {
        self.inner.blocking_lock().set_data_set_reference(&value);
    }

    #[getter]
    fn opt_flds_bits(&self) -> u16 {
        self.inner.blocking_lock().opt_flds().bits()
    }

    #[setter]
    fn set_opt_flds_bits(&self, value: u16) {
        let v = RustReportOptFlds::from_bits_truncate(value);
        self.inner.blocking_lock().set_opt_flds(v);
    }

    #[getter]
    fn buf_tm_ms(&self) -> u32 {
        self.inner.blocking_lock().buf_tm_ms()
    }

    #[setter]
    fn set_buf_tm_ms(&self, value: u32) {
        self.inner.blocking_lock().set_buf_tm_ms(value);
    }

    #[getter]
    fn trg_ops_bits(&self) -> u8 {
        self.inner.blocking_lock().trg_ops().bits()
    }

    #[setter]
    fn set_trg_ops_bits(&self, value: u8) {
        let v = RustTriggerOptions::from_bits_truncate(value);
        self.inner.blocking_lock().set_trg_ops(v);
    }

    #[getter]
    fn intg_pd_ms(&self) -> u32 {
        self.inner.blocking_lock().intg_pd_ms()
    }

    #[setter]
    fn set_intg_pd_ms(&self, value: u32) {
        self.inner.blocking_lock().set_intg_pd_ms(value);
    }

    #[getter]
    fn gi(&self) -> bool {
        self.inner.blocking_lock().gi()
    }

    #[setter]
    fn set_gi(&self, value: bool) {
        self.inner.blocking_lock().set_gi(value);
    }

    #[getter]
    fn purge_buf(&self) -> bool {
        self.inner.blocking_lock().purge_buf()
    }

    #[setter]
    fn set_purge_buf(&self, value: bool) {
        self.inner.blocking_lock().set_purge_buf(value);
    }

    #[getter]
    fn entry_id<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyBytes>> {
        self.inner
            .blocking_lock()
            .entry_id()
            .map(|b| PyBytes::new(py, b))
    }

    #[setter]
    fn set_entry_id(&self, value: Option<Vec<u8>>) -> PyResult<()> {
        self.inner
            .blocking_lock()
            .set_entry_id(value)
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }

    #[getter]
    fn resv_tms(&self) -> i16 {
        self.inner.blocking_lock().resv_tms()
    }

    #[setter]
    fn set_resv_tms(&self, value: i16) {
        self.inner.blocking_lock().set_resv_tms(value);
    }

    #[getter]
    fn has_resv_tms(&self) -> bool {
        self.inner.blocking_lock().has_resv_tms()
    }

    // ── Read-only fields ─────────────────────────────────────────────────────

    #[getter]
    fn conf_rev(&self) -> u32 {
        self.inner.blocking_lock().conf_rev()
    }

    #[getter]
    fn sq_num(&self) -> u16 {
        self.inner.blocking_lock().sq_num()
    }

    #[getter]
    fn time_of_entry_ms(&self) -> u64 {
        self.inner.blocking_lock().time_of_entry_ms()
    }

    #[getter]
    fn owner<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyBytes>> {
        self.inner
            .blocking_lock()
            .owner()
            .map(|b| PyBytes::new(py, b))
    }
}

// ── TypeSpecification / DeviceModel → Python ────────────────────────────────

/// Recursively convert an MMS `TypeSpecification` to a tagged Python dict.
///
/// Every node carries a `"kind"` discriminator; composite kinds carry
/// `"element_type"` (Array) or `"components"` (Structure) sub-dicts.
fn type_spec_to_pydict<'py>(py: Python<'py>, ts: &RustTypeSpec) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    match ts {
        RustTypeSpec::Boolean => {
            d.set_item("kind", "boolean")?;
        }
        RustTypeSpec::UtcTime => {
            d.set_item("kind", "utc_time")?;
        }
        RustTypeSpec::BitString { bits } => {
            d.set_item("kind", "bit_string")?;
            d.set_item("bits", *bits)?;
        }
        RustTypeSpec::Integer { width_bits } => {
            d.set_item("kind", "integer")?;
            d.set_item("width_bits", *width_bits)?;
        }
        RustTypeSpec::Unsigned { width_bits } => {
            d.set_item("kind", "unsigned")?;
            d.set_item("width_bits", *width_bits)?;
        }
        RustTypeSpec::FloatingPoint {
            format_width,
            exponent_width,
        } => {
            d.set_item("kind", "float")?;
            d.set_item("format_width", *format_width)?;
            d.set_item("exponent_width", *exponent_width)?;
        }
        RustTypeSpec::OctetString { max_octets } => {
            d.set_item("kind", "octet_string")?;
            d.set_item("max_octets", *max_octets)?;
        }
        RustTypeSpec::VisibleString { max_chars } => {
            d.set_item("kind", "visible_string")?;
            d.set_item("max_chars", *max_chars)?;
        }
        RustTypeSpec::MmsString { max_chars } => {
            d.set_item("kind", "mms_string")?;
            d.set_item("max_chars", *max_chars)?;
        }
        RustTypeSpec::BinaryTime { use_long_form } => {
            d.set_item("kind", "binary_time")?;
            d.set_item("use_long_form", *use_long_form)?;
        }
        RustTypeSpec::Array {
            element_count,
            element_type,
        } => {
            d.set_item("kind", "array")?;
            d.set_item("element_count", *element_count)?;
            d.set_item("element_type", type_spec_to_pydict(py, element_type)?)?;
        }
        RustTypeSpec::Structure { components } => {
            d.set_item("kind", "structure")?;
            let comps = PyList::empty(py);
            for c in components {
                let comp = PyDict::new(py);
                comp.set_item("name", &c.name)?;
                comp.set_item("type", type_spec_to_pydict(py, &c.type_spec)?)?;
                comps.append(comp)?;
            }
            d.set_item("components", comps)?;
        }
        RustTypeSpec::Unknown(tag) => {
            d.set_item("kind", "unknown")?;
            d.set_item("tag", *tag)?;
        }
    }
    Ok(d)
}

/// Convert a `DeviceModel` (per-LD variable-name index) to a Python dict.
///
/// Shape: `{"logical_devices": [{"name": str, "variables": [str, ...]}, ...]}`.
fn device_model_to_pydict<'py>(
    py: Python<'py>,
    model: &RustDeviceModel,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    let lds = PyList::empty(py);
    for ld in &model.logical_devices {
        let entry = PyDict::new(py);
        entry.set_item("name", &ld.name)?;
        let vars = PyList::empty(py);
        for v in &ld.variables {
            vars.append(v)?;
        }
        entry.set_item("variables", vars)?;
        lds.append(entry)?;
    }
    d.set_item("logical_devices", lds)?;
    Ok(d)
}

// ── MmsValue → Python ────────────────────────────────────────────────────────

/// Convert one MmsValue to its closest native Python representation.
///
/// Scalars become native Python types; composite kinds (Array / Structure)
/// recursively map to ``list`` / ``list`` (caller treats Structure as
/// positional). BitString / OctetString / BinaryTime / UtcTime all surface as
/// ``bytes`` so callers see the wire bytes faithfully.
fn mms_value_to_pyobject(py: Python<'_>, v: &RustMmsValue) -> PyResult<Py<PyAny>> {
    let obj: Py<PyAny> = match v {
        RustMmsValue::Boolean(b) => b.into_pyobject(py)?.to_owned().unbind().into_any(),
        RustMmsValue::Integer(i) => i.into_pyobject(py)?.unbind().into_any(),
        RustMmsValue::Unsigned(u) => u.into_pyobject(py)?.unbind().into_any(),
        RustMmsValue::Float32(f) => (*f).into_pyobject(py)?.unbind().into_any(),
        RustMmsValue::Float64(f) => f.into_pyobject(py)?.unbind().into_any(),
        RustMmsValue::VisibleString(s) | RustMmsValue::MmsString(s) => {
            s.into_pyobject(py)?.unbind().into_any()
        }
        RustMmsValue::OctetString(bytes) | RustMmsValue::BinaryTime(bytes) => {
            PyBytes::new(py, bytes).unbind().into_any()
        }
        RustMmsValue::BitString { data, .. } => PyBytes::new(py, data).unbind().into_any(),
        RustMmsValue::UtcTime(arr) => PyBytes::new(py, arr).unbind().into_any(),
        RustMmsValue::Array(items) | RustMmsValue::Structure(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(mms_value_to_pyobject(py, item)?)?;
            }
            list.unbind().into_any()
        }
    };
    Ok(obj)
}

/// Convert a journal query result into the public Python shape:
/// ``{"entries": [{"entry_id": bytes, "time_ms": int, "variables": [...]}], "more_follows": bool}``.
fn journal_result_to_pydict<'py>(
    py: Python<'py>,
    entries: &[RustJournalEntry],
    more_follows: bool,
) -> PyResult<Bound<'py, PyDict>> {
    let out = PyDict::new(py);
    let list = PyList::empty(py);
    for e in entries {
        let entry = PyDict::new(py);
        entry.set_item("entry_id", PyBytes::new(py, &e.entry_id.0))?;
        entry.set_item("time_ms", e.time_ms)?;
        let vars = PyList::empty(py);
        for v in &e.variables {
            let var = PyDict::new(py);
            var.set_item("data_ref", &v.data_ref)?;
            var.set_item("value", mms_value_to_pyobject(py, &v.value)?)?;
            var.set_item("reason_code", v.reason_code)?;
            vars.append(var)?;
        }
        entry.set_item("variables", vars)?;
        list.append(entry)?;
    }
    out.set_item("entries", list)?;
    out.set_item("more_follows", more_follows)?;
    Ok(out)
}

/// Build the ``dict`` passed to a Python report callback.
///
/// Keys mirror the Rust ``ClientReport`` snapshot — Python side normalises to
/// the public ``ClientReport`` dataclass.
fn client_report_to_pydict<'py>(
    py: Python<'py>,
    report: &RustClientReport,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("rcb_reference", &report.rcb_reference)?;
    d.set_item("rpt_id", report.effective_rpt_id())?;
    d.set_item("dataset_size", report.dataset_size)?;
    d.set_item("sequence_number", report.seq_num)?;
    d.set_item("timestamp_ms", report.timestamp_ms)?;
    d.set_item("data_set_name", &report.data_set_name)?;
    d.set_item("buffer_overflow", report.buf_ovfl)?;
    d.set_item(
        "entry_id",
        report.entry_id.as_deref().map(|b| PyBytes::new(py, b)),
    )?;
    d.set_item("conf_rev", report.conf_rev)?;

    let values = PyList::empty(py);
    for slot in &report.data_set_values {
        let item: Py<PyAny> = match slot {
            Some(v) => mms_value_to_pyobject(py, v)?,
            None => py.None(),
        };
        values.append(item)?;
    }
    d.set_item("values", values)?;

    let reasons = PyList::empty(py);
    for r in &report.reasons {
        reasons.append(r.bits())?;
    }
    d.set_item("reasons", reasons)?;

    let refs = PyList::empty(py);
    for r in &report.data_references {
        match r {
            Some(s) => refs.append(s)?,
            None => refs.append(py.None())?,
        }
    }
    d.set_item("data_references", refs)?;

    Ok(d)
}

// ─────────────────────────────────────────────────────────────────────────────
// SCL / ICD / CID parser surface
//
// Offline file parsing: no network, no async. The SCL XML becomes a Python
// dict shaped like the raw document plus the four DataTypeTemplates tables,
// and a parse failure becomes an SclError carrying line, column,
// element_path, and attribute.
// ─────────────────────────────────────────────────────────────────────────────

/// Map an `SclParseError` into a Python `SclError` exception carrying
/// actionable diagnostic attributes (`line`, `column`, `element_path`,
/// `attribute`, `kind`, `message`).
fn map_scl_error(py: Python<'_>, err: RustSclParseError) -> PyErr {
    let kind_token = match err.kind.as_ref() {
        iec61850_scl::ErrorKind::Xml(_) => "Xml",
        iec61850_scl::ErrorKind::MissingRequiredAttribute { .. } => "MissingRequiredAttribute",
        iec61850_scl::ErrorKind::MissingRequiredElement { .. } => "MissingRequiredElement",
        iec61850_scl::ErrorKind::AttributeValueInvalid { .. } => "AttributeValueInvalid",
        iec61850_scl::ErrorKind::EnumValueUnknown { .. } => "EnumValueUnknown",
        iec61850_scl::ErrorKind::UnresolvedTypeReference { .. } => "UnresolvedTypeReference",
        iec61850_scl::ErrorKind::DuplicateIdentifier { .. } => "DuplicateIdentifier",
        iec61850_scl::ErrorKind::SemanticConflict { .. } => "SemanticConflict",
        iec61850_scl::ErrorKind::Unsupported { .. } => "Unsupported",
    };
    let message = err.to_string();
    let py_err = SclError::new_err(message.clone());
    // Diagnostics ride on the exception instance so a caller can read
    // `e.line` or `e.kind` without parsing the message.
    if let Ok(bound) = py_err.value(py).cast::<PyException>() {
        let _ = bound.setattr("line", err.span.line);
        let _ = bound.setattr("column", err.span.col);
        let _ = bound.setattr("element_path", err.element_path.as_str());
        let _ = bound.setattr("attribute", err.attribute.as_deref());
        let _ = bound.setattr("kind", kind_token);
        let _ = bound.setattr("message", message.as_str());
    }
    py_err
}

fn set_opt_str(d: &Bound<'_, PyDict>, key: &str, val: Option<&str>) -> PyResult<()> {
    match val {
        Some(s) => d.set_item(key, s),
        None => d.set_item(key, Option::<&str>::None),
    }
}

fn trg_ops_to_pydict<'py>(py: Python<'py>, t: &TriggerOptionsBits) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("data_change", t.data_change)?;
    d.set_item("quality_change", t.quality_change)?;
    d.set_item("data_update", t.data_update)?;
    d.set_item("period", t.period)?;
    d.set_item("gi", t.gi)?;
    Ok(d)
}

fn opt_fields_to_pydict<'py>(
    py: Python<'py>,
    o: &OptionFieldsBits,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("seq_num", o.seq_num)?;
    d.set_item("time_stamp", o.time_stamp)?;
    d.set_item("data_set", o.data_set)?;
    d.set_item("reason_code", o.reason_code)?;
    d.set_item("data_ref", o.data_ref)?;
    d.set_item("buffer_overflow", o.buffer_overflow)?;
    d.set_item("ent_id", o.ent_id)?;
    d.set_item("conf_rev", o.conf_rev)?;
    d.set_item("segmentation", o.segmentation)?;
    Ok(d)
}

fn smv_opts_to_pydict<'py>(py: Python<'py>, o: &SmvOptsBits) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("refresh_time", o.refresh_time)?;
    d.set_item("sample_synchronized", o.sample_synchronized)?;
    d.set_item("sample_rate", o.sample_rate)?;
    d.set_item("data_set", o.data_set)?;
    d.set_item("security", o.security)?;
    d.set_item("data_ref", o.data_ref)?;
    Ok(d)
}

fn val_to_pydict<'py>(py: Python<'py>, v: &RawVal) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("s_group", v.s_group)?;
    d.set_item("text", v.raw_text.as_str())?;
    Ok(d)
}

fn dai_to_pydict<'py>(py: Python<'py>, dai: &RawDai) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("kind", "DAI")?;
    d.set_item("name", dai.name.as_str())?;
    d.set_item("ix", dai.ix)?;
    set_opt_str(&d, "val_kind", dai.val_kind.as_deref())?;
    d.set_item("val_import", dai.val_import)?;
    let values = PyList::empty(py);
    for v in &dai.values {
        values.append(val_to_pydict(py, v)?)?;
    }
    d.set_item("values", values)?;
    Ok(d)
}

fn sdi_to_pydict<'py>(py: Python<'py>, sdi: &RawSdi) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("kind", "SDI")?;
    d.set_item("name", sdi.name.as_str())?;
    d.set_item("ix", sdi.ix)?;
    let children = PyList::empty(py);
    for child in &sdi.children {
        children.append(data_instance_to_pydict(py, child)?)?;
    }
    d.set_item("children", children)?;
    Ok(d)
}

fn data_instance_to_pydict<'py>(
    py: Python<'py>,
    inst: &RawDataInstance,
) -> PyResult<Bound<'py, PyDict>> {
    match inst {
        RawDataInstance::Sdi(sdi) => sdi_to_pydict(py, sdi),
        RawDataInstance::Dai(dai) => dai_to_pydict(py, dai),
    }
}

fn doi_to_pydict<'py>(py: Python<'py>, doi: &RawDoi) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", doi.name.as_str())?;
    set_opt_str(&d, "desc", doi.desc.as_deref())?;
    let children = PyList::empty(py);
    for child in &doi.children {
        children.append(data_instance_to_pydict(py, child)?)?;
    }
    d.set_item("children", children)?;
    Ok(d)
}

fn fcda_to_pydict<'py>(py: Python<'py>, f: &RawFcda) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("ld_inst", f.ld_inst.as_str())?;
    set_opt_str(&d, "prefix", f.prefix.as_deref())?;
    d.set_item("ln_class", f.ln_class.as_str())?;
    set_opt_str(&d, "ln_inst", f.ln_inst.as_deref())?;
    set_opt_str(&d, "do_name", f.do_name.as_deref())?;
    set_opt_str(&d, "da_name", f.da_name.as_deref())?;
    d.set_item("fc", f.fc.as_str())?;
    d.set_item("ix", f.ix)?;
    Ok(d)
}

fn data_set_to_pydict<'py>(py: Python<'py>, ds: &RawDataSet) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", ds.name.as_str())?;
    set_opt_str(&d, "desc", ds.desc.as_deref())?;
    let fcdas = PyList::empty(py);
    for f in &ds.fcdas {
        fcdas.append(fcda_to_pydict(py, f)?)?;
    }
    d.set_item("fcdas", fcdas)?;
    Ok(d)
}

fn rcb_to_pydict<'py>(
    py: Python<'py>,
    rcb: &RawReportControlBlock,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", rcb.name.as_str())?;
    set_opt_str(&d, "rpt_id", rcb.rpt_id.as_deref())?;
    set_opt_str(&d, "data_set", rcb.dat_set.as_deref())?;
    d.set_item("conf_rev", rcb.conf_rev)?;
    d.set_item("buffered", rcb.buffered)?;
    d.set_item("intg_pd", rcb.intg_pd)?;
    d.set_item("buf_time", rcb.buf_time)?;
    d.set_item("trg_ops", trg_ops_to_pydict(py, &rcb.trg_ops)?)?;
    d.set_item("opt_fields", opt_fields_to_pydict(py, &rcb.opt_fields)?)?;
    d.set_item("rpt_enabled_max", rcb.rpt_enabled_max)?;
    Ok(d)
}

fn lcb_to_pydict<'py>(py: Python<'py>, lcb: &RawLogControl) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", lcb.name.as_str())?;
    set_opt_str(&d, "data_set", lcb.data_set.as_deref())?;
    set_opt_str(&d, "log_name", lcb.log_name.as_deref())?;
    d.set_item("log_ena", lcb.log_ena)?;
    d.set_item("trg_ops", trg_ops_to_pydict(py, &lcb.trg_ops)?)?;
    d.set_item("intg_pd", lcb.intg_pd)?;
    d.set_item("reason_code", lcb.reason_code)?;
    d.set_item("buf_time", lcb.buf_time)?;
    Ok(d)
}

fn gse_to_pydict<'py>(py: Python<'py>, g: &RawGseControl) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", g.name.as_str())?;
    d.set_item("appl_id", g.appl_id.as_str())?;
    d.set_item("data_set", g.data_set.as_str())?;
    d.set_item("conf_rev", g.conf_rev)?;
    d.set_item("fixed_offs", g.fixed_offs)?;
    let gse_type = match g.gse_type {
        RustGseControlType::Goose => "GOOSE",
        RustGseControlType::GsSe => "GSSE",
    };
    d.set_item("gse_type", gse_type)?;
    Ok(d)
}

fn smv_to_pydict<'py>(py: Python<'py>, s: &RawSampledValueControl) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", s.name.as_str())?;
    d.set_item("smv_id", s.smv_id.as_str())?;
    d.set_item("data_set", s.data_set.as_str())?;
    d.set_item("conf_rev", s.conf_rev)?;
    d.set_item("multicast", s.multicast)?;
    d.set_item("smp_rate", s.smp_rate)?;
    d.set_item("nofasdu", s.nofasdu)?;
    let smp_mod = match s.smp_mod {
        RustSmpMod::SamplesPerPeriod => "SamplesPerPeriod",
        RustSmpMod::SamplesPerSecond => "SamplesPerSecond",
        RustSmpMod::SecondsPerSample => "SecondsPerSample",
    };
    d.set_item("smp_mod", smp_mod)?;
    d.set_item("opts", smv_opts_to_pydict(py, &s.opts)?)?;
    Ok(d)
}

fn setting_control_to_pydict<'py>(
    py: Python<'py>,
    sg: &RawSettingControl,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("num_of_sgs", sg.num_of_sgs)?;
    d.set_item("act_sg", sg.act_sg)?;
    d.set_item("resv_tms", sg.resv_tms)?;
    Ok(d)
}

fn ln_to_pydict<'py>(py: Python<'py>, ln: &RawLogicalNode) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    set_opt_str(&d, "prefix", ln.prefix.as_deref())?;
    d.set_item("ln_class", ln.ln_class.as_str())?;
    d.set_item("inst", ln.inst.as_str())?;
    d.set_item("ln_type", ln.ln_type_ref.as_str())?;
    set_opt_str(&d, "desc", ln.desc.as_deref())?;

    let doi = PyList::empty(py);
    for di in &ln.doi {
        doi.append(doi_to_pydict(py, di)?)?;
    }
    d.set_item("doi", doi)?;

    let datasets = PyList::empty(py);
    for ds in &ln.data_sets {
        datasets.append(data_set_to_pydict(py, ds)?)?;
    }
    d.set_item("data_sets", datasets)?;

    let rcbs = PyList::empty(py);
    for r in &ln.report_controls {
        rcbs.append(rcb_to_pydict(py, r)?)?;
    }
    d.set_item("report_controls", rcbs)?;

    let lcbs = PyList::empty(py);
    for l in &ln.log_controls {
        lcbs.append(lcb_to_pydict(py, l)?)?;
    }
    d.set_item("log_controls", lcbs)?;

    let gses = PyList::empty(py);
    for g in &ln.gse_controls {
        gses.append(gse_to_pydict(py, g)?)?;
    }
    d.set_item("gse_controls", gses)?;

    let smvs = PyList::empty(py);
    for s in &ln.smv_controls {
        smvs.append(smv_to_pydict(py, s)?)?;
    }
    d.set_item("smv_controls", smvs)?;

    match ln.setting_control.as_ref() {
        Some(sg) => d.set_item("setting_control", setting_control_to_pydict(py, sg)?)?,
        None => d.set_item("setting_control", py.None())?,
    }
    Ok(d)
}

fn ld_to_pydict<'py>(py: Python<'py>, ld: &RawLogicalDevice) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("inst", ld.inst.as_str())?;
    set_opt_str(&d, "ld_name", ld.ld_name.as_deref())?;
    set_opt_str(&d, "desc", ld.desc.as_deref())?;
    let lns = PyList::empty(py);
    for ln in &ld.logical_nodes {
        lns.append(ln_to_pydict(py, ln)?)?;
    }
    d.set_item("logical_nodes", lns)?;
    Ok(d)
}

fn server_to_pydict<'py>(py: Python<'py>, srv: &RawServer) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    let lds = PyList::empty(py);
    for ld in &srv.logical_devices {
        lds.append(ld_to_pydict(py, ld)?)?;
    }
    d.set_item("logical_devices", lds)?;
    Ok(d)
}

fn access_point_to_pydict<'py>(
    py: Python<'py>,
    ap: &RawAccessPoint,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", ap.name.as_str())?;
    match ap.server.as_ref() {
        Some(s) => d.set_item("server", server_to_pydict(py, s)?)?,
        None => d.set_item("server", py.None())?,
    }
    Ok(d)
}

fn ied_to_pydict<'py>(py: Python<'py>, ied: &RawIed) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", ied.name.as_str())?;
    set_opt_str(&d, "desc", ied.desc.as_deref())?;
    set_opt_str(&d, "manufacturer", ied.manufacturer.as_deref())?;
    set_opt_str(&d, "config_version", ied.config_version.as_deref())?;
    let aps = PyList::empty(py);
    for ap in &ied.access_points {
        aps.append(access_point_to_pydict(py, ap)?)?;
    }
    d.set_item("access_points", aps)?;
    Ok(d)
}

// The four DataTypeTemplates tables: LNodeType, DOType, DAType, EnumType.

fn bda_to_pydict<'py>(py: Python<'py>, bda: &RawBda) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", bda.name.as_str())?;
    d.set_item("b_type", bda.b_type.as_str())?;
    set_opt_str(&d, "type_ref", bda.type_ref.as_deref())?;
    set_opt_str(&d, "default_value", bda.default_value.as_deref())?;
    set_opt_str(&d, "val_kind", bda.val_kind.as_deref())?;
    let bdas = PyList::empty(py);
    for inner in &bda.bda {
        bdas.append(bda_to_pydict(py, inner)?)?;
    }
    d.set_item("bda", bdas)?;
    Ok(d)
}

fn da_def_to_pydict<'py>(py: Python<'py>, da: &RawDaDef) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", da.name.as_str())?;
    d.set_item("fc", da.fc.as_str())?;
    d.set_item("b_type", da.b_type.as_str())?;
    set_opt_str(&d, "type_ref", da.type_ref.as_deref())?;
    d.set_item("trg_ops", trg_ops_to_pydict(py, &da.trg_ops)?)?;
    d.set_item("count", da.count)?;
    set_opt_str(&d, "default_value", da.default_value.as_deref())?;
    set_opt_str(&d, "val_kind", da.val_kind.as_deref())?;
    let bdas = PyList::empty(py);
    for bda in &da.bda {
        bdas.append(bda_to_pydict(py, bda)?)?;
    }
    d.set_item("bda", bdas)?;
    Ok(d)
}

fn sdo_to_pydict<'py>(py: Python<'py>, sdo: &RawSdoDef) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", sdo.name.as_str())?;
    d.set_item("type", sdo.do_type_ref.as_str())?;
    Ok(d)
}

fn do_def_to_pydict<'py>(py: Python<'py>, do_def: &RawDoDef) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", do_def.name.as_str())?;
    d.set_item("type", do_def.do_type_ref.as_str())?;
    d.set_item("transient", do_def.transient)?;
    set_opt_str(&d, "access_control", do_def.access_control.as_deref())?;
    Ok(d)
}

fn ln_type_to_pydict<'py>(py: Python<'py>, lnt: &RawLNodeType) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("ln_class", lnt.ln_class.as_str())?;
    set_opt_str(&d, "ied_type", lnt.iedtype.as_deref())?;
    let dos = PyList::empty(py);
    for do_def in &lnt.dos {
        dos.append(do_def_to_pydict(py, do_def)?)?;
    }
    d.set_item("dos", dos)?;
    Ok(d)
}

fn do_type_to_pydict<'py>(py: Python<'py>, dot: &RawDoType) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("cdc", dot.cdc.as_str())?;
    let das = PyList::empty(py);
    for da in &dot.das {
        das.append(da_def_to_pydict(py, da)?)?;
    }
    d.set_item("das", das)?;
    let sdos = PyList::empty(py);
    for sdo in &dot.sdos {
        sdos.append(sdo_to_pydict(py, sdo)?)?;
    }
    d.set_item("sdos", sdos)?;
    Ok(d)
}

fn da_type_to_pydict<'py>(py: Python<'py>, dat: &RawDaType) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    let bdas = PyList::empty(py);
    for bda in &dat.bdas {
        bdas.append(bda_to_pydict(py, bda)?)?;
    }
    d.set_item("bdas", bdas)?;
    Ok(d)
}

fn enum_type_to_pydict<'py>(py: Python<'py>, et: &RawEnumType) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    let values = PyList::empty(py);
    for v in &et.values {
        let item = PyDict::new(py);
        item.set_item("ord", v.ord)?;
        item.set_item("name", v.name.as_str())?;
        set_opt_str(&item, "desc", v.desc.as_deref())?;
        values.append(item)?;
    }
    d.set_item("values", values)?;
    Ok(d)
}

fn dtt_to_pydict<'py>(py: Python<'py>, dtt: &RustDtt) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    let ln_types = PyDict::new(py);
    for (id, lnt) in &dtt.ln_node_types {
        ln_types.set_item(id.as_str(), ln_type_to_pydict(py, lnt)?)?;
    }
    d.set_item("ln_node_types", ln_types)?;
    let do_types = PyDict::new(py);
    for (id, dot) in &dtt.do_types {
        do_types.set_item(id.as_str(), do_type_to_pydict(py, dot)?)?;
    }
    d.set_item("do_types", do_types)?;
    let da_types = PyDict::new(py);
    for (id, dat) in &dtt.da_types {
        da_types.set_item(id.as_str(), da_type_to_pydict(py, dat)?)?;
    }
    d.set_item("da_types", da_types)?;
    let enums = PyDict::new(py);
    for (id, et) in &dtt.enum_types {
        enums.set_item(id.as_str(), enum_type_to_pydict(py, et)?)?;
    }
    d.set_item("enum_types", enums)?;
    Ok(d)
}

/// Python-side handle for a parsed (and type-resolved) SCL document.
///
/// Construct via the module-level `parse_scl(xml)` or `load_scl(path)`
/// functions. The handle exposes the document as plain Python dicts plus
/// a canonical text summary for a given IED.
#[pyclass(name = "Scl", module = "iec61850._native", frozen)]
struct PyScl {
    inner: RustResolvedScl,
}

#[pymethods]
impl PyScl {
    /// IED names declared in the document, in source order.
    fn ieds(&self) -> Vec<String> {
        self.inner
            .raw()
            .ieds
            .iter()
            .map(|ied| ied.name.clone())
            .collect()
    }

    /// Full document as a nested dict: `{"ieds": [...], "data_type_templates": {...}}`.
    ///
    /// The shape mirrors the SCL XML structure (type references are kept as
    /// strings; resolve them by looking up `data_type_templates["ln_node_types"][id]`
    /// and the other three template maps).
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let raw = self.inner.raw();
        let d = PyDict::new(py);
        let ieds = PyList::empty(py);
        for ied in &raw.ieds {
            ieds.append(ied_to_pydict(py, ied)?)?;
        }
        d.set_item("ieds", ieds)?;
        d.set_item(
            "data_type_templates",
            dtt_to_pydict(py, &raw.data_type_templates)?,
        )?;
        Ok(d)
    }

    /// Canonical text summary of the runtime model for a specific IED.
    ///
    /// Identical-shape documents produce byte-identical output, making this
    /// suitable as a diff oracle for regression tests.
    fn summary(&self, py: Python<'_>, ied_name: &str) -> PyResult<String> {
        let model = self
            .inner
            .build_model(ied_name)
            .map_err(|e| map_scl_error(py, e))?;
        Ok(iec61850_scl::summarize::summarize_model(&model))
    }

    fn __repr__(&self) -> String {
        let n = self.inner.raw().ieds.len();
        format!("Scl(ieds={n})")
    }
}

/// Parse an SCL / ICD / CID document from an XML string.
///
/// Runs the full two-stage pipeline (syntax → type-reference resolution).
/// Raises `SclError` on failure with `line`, `column`, `element_path`,
/// `attribute`, `kind`, and `message` attributes set.
#[pyfunction]
fn parse_scl(py: Python<'_>, xml: &str) -> PyResult<PyScl> {
    let raw = iec61850_scl::parse_scl(xml).map_err(|e| map_scl_error(py, e))?;
    let resolved = raw.resolve().map_err(|e| map_scl_error(py, e))?;
    Ok(PyScl { inner: resolved })
}

/// Load and parse an SCL / ICD / CID document from a filesystem path.
///
/// The file is read as UTF-8. Raises `SclError` on parse failure (see
/// `parse_scl`) or `OSError` if the file cannot be read.
#[pyfunction]
fn load_scl(py: Python<'_>, path: Bound<'_, PyAny>) -> PyResult<PyScl> {
    let path_str: String = if let Ok(s) = path.cast::<PyString>() {
        s.to_string_lossy().into_owned()
    } else {
        path.call_method0("__fspath__")
            .and_then(|p| p.extract::<String>())
            .map_err(|_| PyValueError::new_err("load_scl: path must be str or os.PathLike[str]"))?
    };
    let xml = std::fs::read_to_string(&path_str)
        .map_err(|e| pyo3::exceptions::PyOSError::new_err(format!("read {path_str}: {e}")))?;
    parse_scl(py, &xml)
}

// ─────────────────────────────────────────────────────────────────────────────
// SNTP client — query a remote SNTP/NTP server for clock offset.
// ─────────────────────────────────────────────────────────────────────────────

fn map_sntp_error(err: RustSntpError) -> PyErr {
    match err {
        RustSntpError::Io(io_err) if io_err.kind() == std::io::ErrorKind::TimedOut => {
            IedTimeoutError::new_err(format!("SNTP query timed out: {io_err}"))
        }
        RustSntpError::Io(io_err) => IedConnectionError::new_err(format!("SNTP I/O: {io_err}")),
        other => IedError::new_err(format!("SNTP error: {other}")),
    }
}

/// Query an SNTP/NTP server for the current time and clock offset.
///
/// Args:
///   server: "host:port" string, e.g. "pool.ntp.org:123" or "10.0.0.1:123".
///           IPv6 literals must be bracketed: "[2001:db8::1]:123".
///   timeout_s: Wall-clock timeout (seconds) waiting for the reply. Default 3.0.
///
/// Returns:
///   An awaitable that resolves to a dict with these keys:
///     - "server_time_unix_s" (float): Server transmit time as Unix seconds.
///     - "offset_seconds"    (float): server - client clock offset, in seconds.
///                                    Positive means the server clock is ahead.
///     - "round_trip_seconds" (float): One-shot round-trip estimate, in seconds.
///     - "stratum"           (int):   Server stratum (1=primary reference, etc.).
///     - "poll"              (int):   Poll interval, log2 seconds.
///     - "precision"         (int):   Server precision, log2 seconds.
///     - "reference_id"      (bytes): 4-byte reference identifier.
///     - "leap_indicator"    (int):   0=no warning, 1=+61s, 2=-59s, 3=alarm.
///     - "version"           (int):   SNTP version echoed by server.
///
/// Raises:
///   IedTimeoutError: No reply within `timeout_s`.
///   IedConnectionError: Socket-level failure (refused, unreachable, etc.).
///   IedError: Malformed reply, kiss-of-death (stratum=0), replay mismatch.
///   ValueError: `server` is not a valid socket address string.
#[pyfunction]
#[pyo3(signature = (server, timeout_s = 3.0))]
fn query_sntp(py: Python<'_>, server: String, timeout_s: f64) -> PyResult<Bound<'_, PyAny>> {
    let addr: SocketAddr = server.parse().map_err(|e| {
        PyValueError::new_err(format!(
            "query_sntp: invalid server address {server:?}: {e}"
        ))
    })?;
    if !(timeout_s.is_finite() && timeout_s > 0.0) {
        return Err(PyValueError::new_err(
            "query_sntp: timeout_s must be a positive finite number",
        ));
    }
    let timeout = Duration::from_secs_f64(timeout_s);
    let client = RustSntpClient::new(addr);
    future_into_py(py, async move {
        let resp = client.query(timeout).await.map_err(map_sntp_error)?;
        Python::attach(|py| {
            let dict = PyDict::new(py);
            dict.set_item("server_time_unix_s", resp.server_time_unix_s)?;
            dict.set_item("client_receive_unix_s", resp.client_receive_unix_s)?;
            dict.set_item("offset_seconds", resp.offset_seconds)?;
            dict.set_item("round_trip_seconds", resp.round_trip_seconds)?;
            dict.set_item("stratum", resp.stratum)?;
            dict.set_item("poll", i32::from(resp.poll))?;
            dict.set_item("precision", i32::from(resp.precision))?;
            dict.set_item("reference_id", PyBytes::new(py, &resp.reference_id))?;
            dict.set_item("leap_indicator", resp.leap_indicator as u8)?;
            dict.set_item("version", resp.version)?;
            Ok::<_, PyErr>(dict.unbind().into_any())
        })
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// IedServer — host an SCL-defined IED as an IEC 61850 MMS server.
// ─────────────────────────────────────────────────────────────────────────────

/// Raw pointer to a `DataAttribute` inside an owned `Arc<IedModel>`.
///
/// SAFETY invariant: every `DaPtr` is paired with an `Arc<IedModel>` held by
/// the same `ServerState::Running` variant. The model tree is never mutated
/// after construction, so the pointee remains valid until the state is
/// replaced (at which point the index is dropped together with the model).
#[derive(Clone, Copy)]
struct DaPtr(*const RustDataAttribute);
// SAFETY: the pointee `DataAttribute` is `Send + Sync` (plain data + `Arc`s).
unsafe impl Send for DaPtr {}
unsafe impl Sync for DaPtr {}

enum ServerState {
    NotStarted {
        bind_addr: Option<SocketAddr>,
        config: RustIedServerConfig,
        pending_handlers: Vec<PendingHandler>,
        pending_tls: Option<PendingTls>,
    },
    Running {
        server: Arc<RustIedServer>,
        handle: Option<RustServerHandle>,
        /// Captured at `start()` from the calling Python event loop;
        /// shared with every control coroutine bridge so the Rust task
        /// can schedule awaits back on the original loop via
        /// `pyo3_async_runtimes::into_future_with_locals`.
        task_locals: TaskLocals,
    },
    Stopped,
}

/// Handler registration queued before `start()`.
///
/// `on_read` / `on_write` / `on_control` may be called while the server is
/// not yet running. Each call is collected here and replayed through
/// `IedServer::install_*_handler` / `control_objects().register(...)` once
/// the server is built.
enum PendingHandler {
    Read {
        canonical: String,
        user_path: String,
        callback: Py<PyAny>,
    },
    Write {
        canonical: String,
        user_path: String,
        callback: Py<PyAny>,
    },
    Control(PendingControl),
    Dataset(PendingDataset),
    Urcb(PendingUrcb),
    Brcb(PendingBrcb),
    LogControl(PendingLogControl),
    SettingGroup(PendingSettingGroupHandler),
}

/// Queued `on_control` registration.
struct PendingControl {
    user_path: String,
    config: RustControlObjectConfig,
    check: Option<Py<PyAny>>,
    operate: Option<Py<PyAny>>,
    wait: Option<Py<PyAny>>,
}

/// Queued `add_dataset` registration.
struct PendingDataset {
    /// Dataset name in the IEC convention `"<LN>$<dsName>"`.
    name: String,
    /// MMS domain (e.g. `"IED1LD0"`) — required to register the
    /// dataset standalone (when no URCB references it).
    domain: String,
    /// Pre-resolved entries (shared `Arc<RwLock<MmsValue>>` with the model
    /// tree) ready to feed into `Dataset::push`.
    entries: Vec<RustDatasetEntry>,
}

/// Queued `register_urcb` registration.
struct PendingUrcb {
    /// Full MMS path `"<domain>/<LN>$RP$<rcb_name>"`.
    mms_path: String,
    /// Dataset reference name (must match an `add_dataset` entry).
    dataset_name: String,
    rcb_name: String,
    rpt_id: String,
    conf_rev: u32,
    trg_ops: RustServerTriggerOptions,
    opt_flds: RustServerOptFlds,
    buf_tm_ms: u32,
    intg_pd_ms: u32,
}

/// Queued `with_tls` configuration. Applied at `start()` to wrap the
/// listener in a `TlsAcceptor`.
struct PendingTls {
    server_cert_pem: Vec<u8>,
    server_key_pem: Vec<u8>,
    client_ca_pem: Option<Vec<u8>>,
    allow_only_known_peers: bool,
    known_peer_pems: Vec<Vec<u8>>,
    chain_validation: bool,
    time_validation: bool,
    crl_pems: Vec<Vec<u8>>,
    session_resumption: bool,
    min_version: iec61850_tls::TlsVersion,
    max_version: iec61850_tls::TlsVersion,
}

/// Queued `register_brcb` registration.
struct PendingBrcb {
    /// Full MMS path `"<domain>/<LN>$BR$<rcb_name>"`.
    mms_path: String,
    dataset_name: String,
    rcb_name: String,
    rpt_id: String,
    conf_rev: u32,
    trg_ops: RustServerTriggerOptions,
    opt_flds: RustServerOptFlds,
    buf_tm_ms: u32,
    intg_pd_ms: u32,
    /// Buffer capacity (entry count, IEC 61850-7-2 §15 IC-2).
    buffer_capacity: usize,
    /// Edition 2+ — expose ResvTms MMS field.
    with_resv_tms: bool,
    /// Edition 2+ — expose Owner MMS field.
    with_owner: bool,
}

/// Queued `register_setting_group_handler` registration.
///
/// The SGCB runtime is materialised by `IedServer::build()` from
/// `IedModel.lds[].lns[].sgcb`, so this entry only needs to ferry the
/// Python callbacks to the running registry. `domain` is the wire-level
/// MMS domain (`iedName + ld.inst`), looked up directly against the
/// shared `SettingGroupRegistry` at `start()`.
struct PendingSettingGroupHandler {
    domain: String,
    /// User-facing path token reported in error messages; usually the
    /// LD instance the caller passed.
    user_path: String,
    on_act_sg: Option<Py<PyAny>>,
    on_edit_sg: Option<Py<PyAny>>,
    on_confirm: Option<Py<PyAny>>,
}

/// Queued `register_log_control` registration.
///
/// LCB triggers are explicit (`log_value`), not auto-driven by `update_*` —
/// see `iec61850_server::logging::LogControl::log_single_value`. The block is
/// materialised at `start()`: an `InMemoryLogStorage` is allocated, the LCB
/// is wired up, and the `(domain, item)` registry entry becomes visible to
/// the MMS dispatcher for `ReadJournal` routing.
struct PendingLogControl {
    /// Full MMS path `"<domain>/<LN>$LG$<lcb_name>"`.
    mms_path: String,
    /// MMS domain (first half of the `(domain, item)` registry key).
    domain: String,
    /// MMS item (second half of the registry key — `"<LN>$LG$<lcb_name>"`).
    item: String,
    lcb_name: String,
    /// Dataset reference recorded on the LCB. Caller supplies the canonical
    /// `"<LN>$<dsName>"` form; the LCB does not enforce that the dataset is
    /// actually registered (Rust-side parity).
    dataset_name: String,
    /// Optional `LogRef`; `None` falls back to the IEC default
    /// (`<LN>$GeneralLog` after the LCB is built).
    log_ref: Option<String>,
    trg_ops: RustServerTriggerOptions,
    intg_period_ms: u32,
    include_reason_code: bool,
    default_enabled: bool,
    /// In-memory storage capacity (entry count). `None` = unbounded.
    storage_capacity: Option<usize>,
}

fn map_server_error(err: RustServerError) -> PyErr {
    use iec61850_server::error::ServerError as SE;
    let msg = err.to_string();
    match err {
        SE::Io(_) => pyo3::exceptions::PyOSError::new_err(msg),
        SE::NotRunning => PyRuntimeError::new_err(msg),
        SE::AlreadyLocked => PyRuntimeError::new_err(msg),
        SE::TypeMismatch { .. } => IedDataAccessError::new_err(msg),
        SE::InvalidCtlModel { .. } => IedDataAccessError::new_err(msg),
        SE::DomainNameTooLong { .. }
        | SE::DuplicateDomain { .. }
        | SE::InvalidModel(_)
        | SE::Protocol(_) => IedServerError::new_err(msg),
    }
}

fn build_path_index(model: &RustIedModel) -> HashMap<String, DaPtr> {
    let mut idx = HashMap::new();
    for ld in &model.lds {
        let ld_inst = ld.inst.as_str();
        for ln in &ld.lns {
            let ln_name = ln.full_name();
            for do_obj in &ln.dos {
                let base = format!("{ld_inst}/{ln_name}.{}", do_obj.name);
                walk_do_children(&do_obj.children, &base, &mut idx);
            }
        }
    }
    idx
}

fn walk_do_children(children: &[RustDoChild], base: &str, idx: &mut HashMap<String, DaPtr>) {
    for child in children {
        match child {
            RustDoChild::Da(da) => {
                let path = format!("{base}.{}", da.name);
                idx.insert(path.clone(), DaPtr(da as *const _));
                walk_da_children(&da.children, &path, idx);
            }
            RustDoChild::SubDo(sub) => {
                let sub_base = format!("{base}.{}", sub.name);
                walk_do_children(&sub.children, &sub_base, idx);
            }
        }
    }
}

fn walk_da_children(children: &[RustDataAttribute], base: &str, idx: &mut HashMap<String, DaPtr>) {
    for da in children {
        let path = format!("{base}.{}", da.name);
        idx.insert(path.clone(), DaPtr(da as *const _));
        walk_da_children(&da.children, &path, idx);
    }
}

fn extract_path_arg(path: Bound<'_, PyAny>, caller: &str) -> PyResult<String> {
    if let Ok(s) = path.cast::<PyString>() {
        return Ok(s.to_string_lossy().into_owned());
    }
    path.call_method0("__fspath__")
        .and_then(|p| p.extract::<String>())
        .map_err(|_| {
            PyValueError::new_err(format!("{caller}: path must be str or os.PathLike[str]"))
        })
}

async fn server_do_start(
    state_arc: &Arc<std::sync::Mutex<ServerState>>,
    model: &Arc<RustIedModel>,
    task_locals: TaskLocals,
) -> PyResult<()> {
    let (bind_addr, config, pending, pending_tls) = {
        let mut g = state_arc.lock().unwrap();
        match std::mem::replace(&mut *g, ServerState::Stopped) {
            ServerState::NotStarted {
                bind_addr,
                config,
                pending_handlers,
                pending_tls,
            } => {
                let Some(addr) = bind_addr else {
                    *g = ServerState::NotStarted {
                        bind_addr: None,
                        config,
                        pending_handlers,
                        pending_tls,
                    };
                    return Err(PyRuntimeError::new_err(
                        "start: bind() must be called first",
                    ));
                };
                (addr, config, pending_handlers, pending_tls)
            }
            other => {
                *g = other;
                return Err(PyRuntimeError::new_err(
                    "start: server has already been started",
                ));
            }
        }
    };

    let mut builder = RustIedServer::builder()
        .model(Arc::clone(model))
        .bind(bind_addr)
        .config(config);
    if let Some(pt) = pending_tls {
        builder = builder.with_tls(build_tls_acceptor(&pt)?);
    }
    let server = builder.build().map_err(map_server_error)?;
    let server_arc = Arc::new(server);

    // Bucket pending entries by kind so reporting registrations happen in
    // the right order: datasets first, then URCBs / BRCBs (which consume them).
    let mut pending_datasets: Vec<PendingDataset> = Vec::new();
    let mut pending_urcbs: Vec<PendingUrcb> = Vec::new();
    let mut pending_brcbs: Vec<PendingBrcb> = Vec::new();
    let mut pending_lcbs: Vec<PendingLogControl> = Vec::new();
    let mut pending_sgcb_handlers: Vec<PendingSettingGroupHandler> = Vec::new();
    for entry in pending {
        match entry {
            PendingHandler::Read {
                canonical,
                user_path,
                callback,
            } => {
                let h: Arc<dyn RustReadHandler> = Arc::new(PyReadHandler {
                    user_path,
                    callback,
                });
                server_arc
                    .install_read_handler(&canonical, h)
                    .map_err(map_server_error)?;
            }
            PendingHandler::Write {
                canonical,
                user_path,
                callback,
            } => {
                let h: Arc<dyn RustWriteHandler> = Arc::new(PyWriteHandler {
                    user_path,
                    callback,
                });
                server_arc
                    .install_write_access_handler(&canonical, h)
                    .map_err(map_server_error)?;
            }
            PendingHandler::Control(pc) => {
                let entry = build_control_entry(pc, task_locals.clone());
                server_arc.control_objects().register(entry);
            }
            PendingHandler::Dataset(pd) => pending_datasets.push(pd),
            PendingHandler::Urcb(urcb) => pending_urcbs.push(urcb),
            PendingHandler::Brcb(brcb) => pending_brcbs.push(brcb),
            PendingHandler::LogControl(plcb) => pending_lcbs.push(plcb),
            PendingHandler::SettingGroup(psg) => pending_sgcb_handlers.push(psg),
        }
    }

    // First pass: register every URCB together with its dataset. The Rust
    // crate seeds `attr_ref_index` from the dataset entries during this
    // call, so URCB triggering works post-start.
    let mut referenced: std::collections::HashSet<String> = std::collections::HashSet::new();
    for urcb in pending_urcbs {
        let pd = pending_datasets
            .iter()
            .find(|d| d.name == urcb.dataset_name)
            .ok_or_else(|| {
                PyKeyError::new_err(format!(
                    "start: URCB '{}' references unknown dataset '{}'",
                    urcb.mms_path, urcb.dataset_name
                ))
            })?;
        let ds = build_dataset(pd);
        let rc = RustReportControl::new(urcb.mms_path.as_str(), build_rcb(&urcb));
        server_arc.register_urcb(rc, ds).map_err(map_server_error)?;
        referenced.insert(urcb.dataset_name.clone());
    }
    // Second pass: BRCBs bind to the same dataset pool. `register_brcb` seeds
    // attr_ref_index / dataset_registry the same way `register_urcb` does.
    for brcb in pending_brcbs {
        let pd = pending_datasets
            .iter()
            .find(|d| d.name == brcb.dataset_name)
            .ok_or_else(|| {
                PyKeyError::new_err(format!(
                    "start: BRCB '{}' references unknown dataset '{}'",
                    brcb.mms_path, brcb.dataset_name
                ))
            })?;
        let ds = build_dataset(pd);
        let brc = RustBufferedReportControl::new(brcb.mms_path.as_str(), build_brcb(&brcb));
        server_arc
            .register_brcb(brc, ds)
            .map_err(map_server_error)?;
        referenced.insert(brcb.dataset_name.clone());
    }
    // Third pass: any dataset not bound to a URCB / BRCB is still exposed
    // via `GetDataSetValues` by calling the standalone register API.
    for pd in &pending_datasets {
        if !referenced.contains(&pd.name) {
            let ds = build_dataset(pd);
            server_arc.register_dataset(pd.domain.clone(), ds);
        }
    }
    // LCBs: allocate an in-memory `LogStorage` per block, wire the LCB, and
    // hand the `Arc<LogControl>` to the server registry. The dataset is recorded
    // by name only — the Rust-side trigger path is explicit (`log_value`).
    for plcb in pending_lcbs {
        let lc = build_log_control(&plcb)?;
        server_arc.register_log_control(plcb.domain.clone(), plcb.item.clone(), Arc::new(lc));
    }
    // SGCB runtime entries are populated from the model at `build()`, so
    // only the Python-side callbacks remain to be wired into the matching
    // domain. `register_setting_group_handler` surfaces `InvalidModel` when
    // the LD has no SGCB.
    for psg in pending_sgcb_handlers {
        let user_path = psg.user_path.clone();
        let handler: Arc<dyn RustSettingGroupHandler> = Arc::new(PySettingGroupHandler {
            on_act_sg: psg.on_act_sg,
            on_edit_sg: psg.on_edit_sg,
            on_confirm: psg.on_confirm,
        });
        server_arc
            .register_setting_group_handler(&psg.domain, handler)
            .map_err(|_| {
                PyKeyError::new_err(format!(
                    "start: register_setting_group_handler: LD '{user_path}' has no SGCB"
                ))
            })?;
    }

    let handle = server_arc.start().await.map_err(map_server_error)?;

    *state_arc.lock().unwrap() = ServerState::Running {
        server: server_arc,
        handle: Some(handle),
        task_locals,
    };
    Ok(())
}

async fn server_do_stop(state_arc: &Arc<std::sync::Mutex<ServerState>>) -> PyResult<()> {
    let handle = {
        let mut g = state_arc.lock().unwrap();
        match std::mem::replace(&mut *g, ServerState::Stopped) {
            ServerState::Running { handle, .. } => handle,
            other => {
                *g = other;
                return Err(PyRuntimeError::new_err("stop: server is not running"));
            }
        }
    };
    if let Some(h) = handle {
        h.stop().await;
    }
    Ok(())
}

/// Hosts an IEC 61850 IED as an MMS server.
///
/// Construct with `from_scl(path, ied_name=...)` (or `from_scl_str` for an
/// inline XML string), configure with `bind()` and the property setters,
/// then enter the runtime via `start()` / `stop()` or
/// `async with server: ...`.
///
/// While running, push value updates with the typed `update_*` methods,
/// addressing data attributes by `"<LD>/<LN>.<DO>.<DA>[.<sub>]*"`.
#[pyclass(name = "IedServer", module = "iec61850._native", frozen)]
struct PyIedServer {
    state: Arc<std::sync::Mutex<ServerState>>,
    /// Owned anchor for every `DaPtr` in `path_index`. The model tree is
    /// constructed once at `from_scl_*` and never mutated; pointers are valid
    /// for the lifetime of this `Arc`.
    model: Arc<RustIedModel>,
    path_index: Arc<HashMap<String, DaPtr>>,
}

impl PyIedServer {
    /// Wrap an already-built `Arc<RustIedModel>` into a fresh `PyIedServer`
    /// in the `NotStarted` state. Shared by `from_scl_str`,
    /// `from_model_spec`, and any future model-driven entry point.
    fn from_model_arc(model: Arc<RustIedModel>) -> Self {
        let path_index = Arc::new(build_path_index(&model));
        PyIedServer {
            state: Arc::new(std::sync::Mutex::new(ServerState::NotStarted {
                bind_addr: None,
                config: RustIedServerConfig::default(),
                pending_handlers: Vec::new(),
                pending_tls: None,
            })),
            model,
            path_index,
        }
    }

    fn with_da<F, R>(&self, caller: &str, path: &str, f: F) -> PyResult<R>
    where
        F: FnOnce(&RustIedServer, &RustDataAttribute) -> Result<R, RustServerError>,
    {
        let ptr = self
            .path_index
            .get(path)
            .copied()
            .ok_or_else(|| PyKeyError::new_err(format!("{caller}: path '{path}' not found")))?;
        // SAFETY: see `DaPtr` invariant — `self.model` pins the tree alive.
        let da: &RustDataAttribute = unsafe { &*ptr.0 };
        let state = self.state.lock().unwrap();
        match &*state {
            ServerState::Running { server, .. } => f(server.as_ref(), da).map_err(map_server_error),
            _ => Err(PyRuntimeError::new_err(format!(
                "{caller}: server is not running"
            ))),
        }
    }

    /// Resolve a control-object path `"LD/LN.DO"` into the MMS-level
    /// `(domain, ln_name, do_name)` triple expected by the control
    /// registry. `domain` is `LogicalDevice::ld_name` if set, otherwise
    /// `ied_name + ld_inst`.
    fn resolve_control_path(
        &self,
        caller: &str,
        user_path: &str,
    ) -> PyResult<(String, String, String)> {
        let (ld_inst, rest) = user_path.split_once('/').ok_or_else(|| {
            PyValueError::new_err(format!(
                "{caller}: path '{user_path}' must be '<LD>/<LN>.<DO>'"
            ))
        })?;
        let (ln_name, do_name) = rest.split_once('.').ok_or_else(|| {
            PyValueError::new_err(format!(
                "{caller}: path '{user_path}' missing '.<DO>' segment"
            ))
        })?;
        if do_name.contains('.') {
            return Err(PyValueError::new_err(format!(
                "{caller}: control path '{user_path}' must address a DO, not a DA"
            )));
        }
        let ld = self
            .model
            .lds
            .iter()
            .find(|ld| ld.inst == ld_inst)
            .ok_or_else(|| {
                PyKeyError::new_err(format!(
                    "{caller}: logical device '{ld_inst}' not found in model"
                ))
            })?;
        let domain = ld.domain_name(&self.model.ied_name);
        Ok((domain, ln_name.to_string(), do_name.to_string()))
    }

    /// Build a `DatasetEntry` from a user-facing `"LD/LN.DO.DA[.sub]*"`
    /// path. Also returns the MMS domain (e.g. `"IED1LD0"`) so callers
    /// can validate that every entry in a dataset belongs to the same LD.
    fn build_dataset_entry(
        &self,
        caller: &str,
        user_path: &str,
    ) -> PyResult<(RustDatasetEntry, String)> {
        let canonical = self.canonical_handler_path(caller, user_path)?;
        let (ld_inst, _) = user_path.split_once('/').ok_or_else(|| {
            PyValueError::new_err(format!(
                "{caller}: path '{user_path}' missing '<LD>/' prefix"
            ))
        })?;
        let ld = self
            .model
            .lds
            .iter()
            .find(|ld| ld.inst == ld_inst)
            .ok_or_else(|| {
                PyKeyError::new_err(format!(
                    "{caller}: logical device '{ld_inst}' not found in model"
                ))
            })?;
        let domain = ld.domain_name(&self.model.ied_name);
        let attr_ref = format!("{domain}/{canonical}");
        let ptr = self.path_index.get(user_path).copied().ok_or_else(|| {
            PyKeyError::new_err(format!("{caller}: path '{user_path}' not found"))
        })?;
        // SAFETY: see `DaPtr` invariant — `self.model` pins the tree alive.
        let da: &RustDataAttribute = unsafe { &*ptr.0 };
        Ok((
            RustDatasetEntry::new(attr_ref, Arc::clone(&da.value)),
            domain,
        ))
    }

    /// Resolve a URCB path `"<LD>/<LN>.<rcb_name>"` into
    /// `(domain, ln_name, rcb_name, mms_path)`. `mms_path` is the wire-level
    /// key `"<domain>/<LN>$RP$<rcb_name>"` expected by `register_urcb`.
    fn resolve_urcb_path(
        &self,
        caller: &str,
        user_path: &str,
    ) -> PyResult<(String, String, String, String)> {
        self.resolve_rcb_path(caller, user_path, "RP")
    }

    /// Resolve a BRCB path `"<LD>/<LN>.<rcb_name>"` into
    /// `(domain, ln_name, rcb_name, mms_path)` with the BRCB `$BR$` separator.
    fn resolve_brcb_path(
        &self,
        caller: &str,
        user_path: &str,
    ) -> PyResult<(String, String, String, String)> {
        self.resolve_rcb_path(caller, user_path, "BR")
    }

    /// Resolve an LCB path `"<LD>/<LN>.<lcb_name>"` into
    /// `(domain, ln_name, lcb_name, mms_path)` with the Log Control `$LG$` separator.
    fn resolve_lcb_path(
        &self,
        caller: &str,
        user_path: &str,
    ) -> PyResult<(String, String, String, String)> {
        self.resolve_rcb_path(caller, user_path, "LG")
    }

    /// Resolve an LD identifier (`"<ld_inst>"`) into the MMS-level domain
    /// (`iedName + ld_inst`, or the `ldName` override when set).
    fn resolve_ld_domain(&self, caller: &str, ld_inst: &str) -> PyResult<String> {
        let ld = self
            .model
            .lds
            .iter()
            .find(|ld| ld.inst == ld_inst)
            .ok_or_else(|| {
                PyKeyError::new_err(format!(
                    "{caller}: logical device '{ld_inst}' not found in model"
                ))
            })?;
        Ok(ld.domain_name(&self.model.ied_name))
    }

    /// Shared backing for URCB / BRCB path resolution. `fc` is `"RP"` or
    /// `"BR"` and controls the MMS path separator (`$RP$` vs `$BR$`).
    fn resolve_rcb_path(
        &self,
        caller: &str,
        user_path: &str,
        fc: &str,
    ) -> PyResult<(String, String, String, String)> {
        let (ld_inst, rest) = user_path.split_once('/').ok_or_else(|| {
            PyValueError::new_err(format!(
                "{caller}: path '{user_path}' must be '<LD>/<LN>.<rcb_name>'"
            ))
        })?;
        let (ln_name, rcb_name) = rest.split_once('.').ok_or_else(|| {
            PyValueError::new_err(format!(
                "{caller}: path '{user_path}' missing '.<rcb_name>' segment"
            ))
        })?;
        if rcb_name.is_empty() || rcb_name.contains('.') {
            return Err(PyValueError::new_err(format!(
                "{caller}: path '{user_path}' must address a single RCB name"
            )));
        }
        let ld = self
            .model
            .lds
            .iter()
            .find(|ld| ld.inst == ld_inst)
            .ok_or_else(|| {
                PyKeyError::new_err(format!(
                    "{caller}: logical device '{ld_inst}' not found in model"
                ))
            })?;
        let domain = ld.domain_name(&self.model.ied_name);
        let mms_path = format!("{domain}/{ln_name}${fc}${rcb_name}");
        Ok((domain, ln_name.to_string(), rcb_name.to_string(), mms_path))
    }

    /// Translate a user-facing `"LD/LN.DO.DA[.sub]*"` path into the
    /// canonical handler key `"LN$FC$DO[$DA]*"` used by the server-side
    /// handler registry. Returns `KeyError` if the DA is unknown.
    fn canonical_handler_path(&self, caller: &str, user_path: &str) -> PyResult<String> {
        let ptr = self.path_index.get(user_path).copied().ok_or_else(|| {
            PyKeyError::new_err(format!("{caller}: path '{user_path}' not found"))
        })?;
        // SAFETY: see `DaPtr` invariant.
        let da: &RustDataAttribute = unsafe { &*ptr.0 };
        let (_ld, rest) = user_path.split_once('/').ok_or_else(|| {
            PyValueError::new_err(format!(
                "{caller}: path '{user_path}' missing '<LD>/' prefix"
            ))
        })?;
        let mut parts = rest.split('.');
        let ln = parts.next().ok_or_else(|| {
            PyValueError::new_err(format!("{caller}: path '{user_path}' missing LN segment"))
        })?;
        let tail: Vec<&str> = parts.collect();
        if tail.is_empty() {
            return Err(PyValueError::new_err(format!(
                "{caller}: path '{user_path}' missing DO segment"
            )));
        }
        Ok(format!("{ln}${}${}", da.fc.as_str(), tail.join("$")))
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Python → Rust handler bridge
// ─────────────────────────────────────────────────────────────────────────────

/// Default `DataAccessError` used when a Python callback raises an exception
/// that does not carry an explicit `code` attribute.
const DEFAULT_HANDLER_ERROR: RustDataAccessError = RustDataAccessError::ObjectAccessDenied;

/// Map a `DataAccessError` enum variant from its symbolic Python name.
///
/// Accepts the variant identifier (e.g. ``"HardwareFault"``,
/// ``"ObjectAccessDenied"``) or the numeric MMS code (0-11). Anything else
/// falls back to [`DEFAULT_HANDLER_ERROR`].
fn data_access_from_py(name: &str) -> RustDataAccessError {
    match name {
        "ObjectInvalidated" => RustDataAccessError::ObjectInvalidated,
        "HardwareFault" => RustDataAccessError::HardwareFault,
        "TemporarilyUnavailable" => RustDataAccessError::TemporarilyUnavailable,
        "ObjectAccessDenied" => RustDataAccessError::ObjectAccessDenied,
        "ObjectUndefined" => RustDataAccessError::ObjectUndefined,
        "InvalidAddress" => RustDataAccessError::InvalidAddress,
        "TypeUnsupported" => RustDataAccessError::TypeUnsupported,
        "TypeInconsistent" => RustDataAccessError::TypeInconsistent,
        "ObjectAttributeInconsistent" => RustDataAccessError::ObjectAttributeInconsistent,
        "ObjectAccessUnsupported" => RustDataAccessError::ObjectAccessUnsupported,
        "ObjectNonExistent" => RustDataAccessError::ObjectNonExistent,
        "ObjectValueInvalid" => RustDataAccessError::ObjectValueInvalid,
        _ => DEFAULT_HANDLER_ERROR,
    }
}

/// Extract a `DataAccessError` from a Python exception raised by a handler.
///
/// Callers may set the ``code`` attribute on the raised exception (typically
/// `IedDataAccessError`) to one of the symbolic names above or its numeric
/// MMS code. The exception is printed via the standard hook so tracebacks
/// stay observable.
fn py_err_to_data_access(py: Python<'_>, err: &PyErr) -> RustDataAccessError {
    let value = err.value(py);
    if let Ok(code_attr) = value.getattr("code") {
        if let Ok(s) = code_attr.extract::<String>() {
            return data_access_from_py(&s);
        }
        if let Ok(n) = code_attr.extract::<u8>() {
            if let Ok(e) = RustDataAccessError::from_code(n) {
                return e;
            }
        }
    }
    err.clone_ref(py).print(py);
    DEFAULT_HANDLER_ERROR
}

/// Rust-side adapter that calls a Python callable for each read.
struct PyReadHandler {
    user_path: String,
    callback: Py<PyAny>,
}

impl std::fmt::Debug for PyReadHandler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PyReadHandler")
            .field("user_path", &self.user_path)
            .finish()
    }
}

impl RustReadHandler for PyReadHandler {
    fn read(&self, _ctx: &RustReadContext<'_>) -> RustReadOutcome {
        Python::attach(|py| {
            let cb = self.callback.bind(py);
            match cb.call1((self.user_path.as_str(),)) {
                Ok(result) => {
                    if result.is_none() {
                        RustReadOutcome::CacheMiss
                    } else {
                        match py_value_to_mms_value_generic(&result) {
                            Ok(v) => RustReadOutcome::CacheHit(v),
                            Err(_) => RustReadOutcome::Error(RustDataAccessError::TypeInconsistent),
                        }
                    }
                }
                Err(py_err) => RustReadOutcome::Error(py_err_to_data_access(py, &py_err)),
            }
        })
    }
}

/// Rust-side adapter that calls a Python callable for each write.
struct PyWriteHandler {
    user_path: String,
    callback: Py<PyAny>,
}

impl std::fmt::Debug for PyWriteHandler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PyWriteHandler")
            .field("user_path", &self.user_path)
            .finish()
    }
}

impl RustWriteHandler for PyWriteHandler {
    fn on_write(&self, _ctx: &RustWriteContext<'_>, value: &RustMmsValue) -> RustWriteOutcome {
        Python::attach(|py| {
            let py_value = match mms_value_to_pyobject(py, value) {
                Ok(v) => v,
                Err(_) => return RustWriteOutcome::Reject(RustDataAccessError::TypeInconsistent),
            };
            let cb = self.callback.bind(py);
            match cb.call1((self.user_path.as_str(), py_value)) {
                Ok(result) => {
                    if result.is_none() {
                        RustWriteOutcome::AcceptNoUpdate
                    } else {
                        match result.extract::<bool>() {
                            Ok(true) => RustWriteOutcome::Accept,
                            Ok(false) => RustWriteOutcome::AcceptNoUpdate,
                            Err(_) => {
                                RustWriteOutcome::Reject(RustDataAccessError::TypeInconsistent)
                            }
                        }
                    }
                }
                Err(py_err) => RustWriteOutcome::Reject(py_err_to_data_access(py, &py_err)),
            }
        })
    }
}

/// Rust-side adapter that bridges three SGCB events to Python callables.
///
/// Each callable is independently optional; an unset slot falls back to
/// the default `allow` / no-op behavior. Callback signatures:
///
/// - `on_act_sg(new_sg: int, conn_id: int) -> bool` — return `False` to
///   reject (`ObjectAccessDenied`).
/// - `on_edit_sg(new_sg: int, conn_id: int) -> bool` — same veto contract;
///   `new_sg == 0` means client-side cancel.
/// - `on_confirm(edit_sg: int, conn_id: int) -> None` — notification only,
///   no veto.
struct PySettingGroupHandler {
    on_act_sg: Option<Py<PyAny>>,
    on_edit_sg: Option<Py<PyAny>>,
    on_confirm: Option<Py<PyAny>>,
}

impl RustSettingGroupHandler for PySettingGroupHandler {
    fn act_sg_changed(&self, new_act_sg: u8, conn_id: RustConnectionId) -> bool {
        sgcb_bool_callback(self.on_act_sg.as_ref(), new_act_sg, conn_id)
    }

    fn edit_sg_changed(&self, new_edit_sg: u8, conn_id: RustConnectionId) -> bool {
        sgcb_bool_callback(self.on_edit_sg.as_ref(), new_edit_sg, conn_id)
    }

    fn confirm_edit_sg(&self, edit_sg: u8, conn_id: RustConnectionId) {
        if let Some(cb) = self.on_confirm.as_ref() {
            Python::attach(|py| {
                let bound = cb.bind(py);
                if let Err(err) = bound.call1((edit_sg, conn_id)) {
                    err.write_unraisable(py, Some(bound));
                }
            });
        }
    }
}

fn sgcb_bool_callback(cb: Option<&Py<PyAny>>, new_value: u8, conn_id: RustConnectionId) -> bool {
    let Some(cb) = cb else { return true };
    Python::attach(|py| {
        let bound = cb.bind(py);
        match bound.call1((new_value, conn_id)) {
            Ok(result) => {
                if result.is_none() {
                    true
                } else {
                    // Non-bool returns are treated as veto so the application
                    // gets a clear `ObjectAccessDenied` rather than a silent
                    // accept; the surfaced Python warning is enough to diagnose.
                    result.extract::<bool>().unwrap_or(false)
                }
            }
            Err(err) => {
                err.write_unraisable(py, Some(bound));
                false
            }
        }
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Control handler bridge
// ─────────────────────────────────────────────────────────────────────────────

/// Map a `ControlAddCause` variant name (e.g. ``"BlockedByInterlocking"``)
/// or numeric code (0-27) back to the enum.
fn control_add_cause_from_str(name: &str) -> Option<RustServerControlAddCause> {
    let v = match name {
        "Unknown" => 0,
        "NotSupported" => 1,
        "BlockedBySwitchingHierarchy" => 2,
        "SelectFailed" => 3,
        "InvalidPosition" => 4,
        "PositionReached" => 5,
        "ParameterChangeInExecution" => 6,
        "StepLimit" => 7,
        "BlockedByMode" => 8,
        "BlockedByProcess" => 9,
        "BlockedByInterlocking" => 10,
        "BlockedBySynchroCheck" => 11,
        "CommandAlreadyInExecution" => 12,
        "BlockedByHealth" => 13,
        "OneOfNControl" => 14,
        "AbortionByCancel" => 15,
        "TimeLimitOver" => 16,
        "AbortionByTrip" => 17,
        "ObjectNotSelected" => 18,
        "ObjectAlreadySelected" => 19,
        "NoAccessAuthority" => 20,
        "EndedWithOvershoot" => 21,
        "AbortionDueToDeviation" => 22,
        "AbortionByCommunicationLoss" => 23,
        "AbortionByCommand" => 24,
        "None" => 25,
        "InconsistentParameters" => 26,
        "LockedByOtherClient" => 27,
        _ => return None,
    };
    Some(RustServerControlAddCause::from_i32(v))
}

/// Extract a `ControlAddCause` from a Python exception raised by a control
/// callback. Callers set ``add_cause`` on the raised exception (typically
/// `IedControlError`) to the variant name (`"BlockedByInterlocking"`) or
/// numeric MMS code; absence falls back to `Unknown`.
fn py_err_to_add_cause(py: Python<'_>, err: &PyErr) -> RustServerControlAddCause {
    let value = err.value(py);
    if let Ok(attr) = value.getattr("add_cause") {
        if let Ok(s) = attr.extract::<String>() {
            if let Some(c) = control_add_cause_from_str(&s) {
                return c;
            }
        }
        if let Ok(n) = attr.extract::<i32>() {
            return RustServerControlAddCause::from_i32(n);
        }
    }
    err.clone_ref(py).print(py);
    RustServerControlAddCause::Unknown
}

/// Build the dict passed to Python control callbacks describing the
/// `ControlAction`. Keys: ``ctl_num`` / ``test`` / ``synchro_check`` /
/// ``interlock_check`` / ``is_select`` / ``ctl_time_ms`` / ``origin``.
fn control_action_to_pydict<'py>(
    py: Python<'py>,
    action: &RustControlAction,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("ctl_num", action.ctl_num())?;
    d.set_item("test", action.test())?;
    d.set_item("synchro_check", action.synchro_check())?;
    d.set_item("interlock_check", action.interlock_check())?;
    d.set_item("is_select", action.is_select())?;
    d.set_item("ctl_time_ms", action.ctl_time_ms())?;
    let origin = action.origin();
    let origin_dict = PyDict::new(py);
    origin_dict.set_item("or_cat", origin.or_cat)?;
    origin_dict.set_item("or_ident", PyBytes::new(py, &origin.or_ident))?;
    d.set_item("origin", origin_dict)?;
    Ok(d)
}

/// Interpret the value returned by a control callback (either the direct
/// return for a sync callable, or the resolved value of an awaited
/// coroutine).
///
/// Conventions:
/// - ``None`` / ``True`` → success.
/// - ``False`` → reject with `Unknown` cause (caller should usually raise
///   to provide a specific cause).
/// - anything else → success.
fn interpret_handler_outcome(value: &Bound<'_, PyAny>) -> Result<(), RustServerControlAddCause> {
    if let Ok(false) = value.extract::<bool>() {
        return Err(RustServerControlAddCause::Unknown);
    }
    Ok(())
}

/// Drives a Python control callable: enters the GIL, calls the callable,
/// detects whether the return is a coroutine, and (for async callbacks)
/// converts it to a Rust future scheduled on the captured Python event
/// loop via `pyo3_async_runtimes::into_future_with_locals`.
///
/// Returns either a fully-resolved `Result<(), ControlAddCause>` for sync
/// callbacks or a pinned future to await for coroutines.
fn invoke_control_callable<F>(
    callback: &Py<PyAny>,
    task_locals: &TaskLocals,
    args_builder: F,
) -> Result<HandlerStep, RustServerControlAddCause>
where
    F: for<'py> FnOnce(Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyTuple>>,
{
    Python::attach(|py| {
        let cb = callback.bind(py);
        let args = match args_builder(py) {
            Ok(a) => a,
            Err(e) => return Err(py_err_to_add_cause(py, &e)),
        };
        let result = match cb.call1(args) {
            Ok(r) => r,
            Err(e) => return Err(py_err_to_add_cause(py, &e)),
        };
        match result.hasattr("__await__") {
            Ok(true) => match pyo3_async_runtimes::into_future_with_locals(task_locals, result) {
                Ok(fut) => Ok(HandlerStep::Future(Box::pin(fut))),
                Err(e) => Err(py_err_to_add_cause(py, &e)),
            },
            _ => Ok(HandlerStep::Resolved(interpret_handler_outcome(&result))),
        }
    })
}

enum HandlerStep {
    Resolved(Result<(), RustServerControlAddCause>),
    Future(Pin<Box<dyn Future<Output = PyResult<Py<PyAny>>> + Send>>),
}

/// Await an already-resolved or pending handler step.
async fn finish_handler_step(
    step: Result<HandlerStep, RustServerControlAddCause>,
) -> Result<(), RustServerControlAddCause> {
    match step {
        Err(cause) => Err(cause),
        Ok(HandlerStep::Resolved(r)) => r,
        Ok(HandlerStep::Future(fut)) => match fut.await {
            Ok(val) => Python::attach(|py| interpret_handler_outcome(val.bind(py))),
            Err(py_err) => Python::attach(|py| Err(py_err_to_add_cause(py, &py_err))),
        },
    }
}

struct PyCheckHandler {
    user_path: String,
    callback: Py<PyAny>,
}

impl RustCheckHandler for PyCheckHandler {
    fn check(
        &self,
        action: &RustControlAction,
        ctl_val: Option<&RustMmsValue>,
        _test: bool,
        _interlock_check: bool,
    ) -> Result<(), RustServerControlAddCause> {
        Python::attach(|py| {
            let action_dict = match control_action_to_pydict(py, action) {
                Ok(d) => d,
                Err(e) => return Err(py_err_to_add_cause(py, &e)),
            };
            let ctl_val_py: Py<PyAny> = match ctl_val {
                Some(v) => match mms_value_to_pyobject(py, v) {
                    Ok(p) => p,
                    Err(e) => return Err(py_err_to_add_cause(py, &e)),
                },
                None => py.None(),
            };
            let cb = self.callback.bind(py);
            match cb.call1((self.user_path.as_str(), ctl_val_py, action_dict)) {
                Ok(_) => Ok(()),
                Err(py_err) => Err(py_err_to_add_cause(py, &py_err)),
            }
        })
    }
}

struct PyControlOperateHandler {
    user_path: String,
    callback: Py<PyAny>,
    task_locals: TaskLocals,
}

impl RustControlHandler for PyControlOperateHandler {
    fn operate<'a>(
        &'a self,
        action: &'a RustControlAction,
        ctl_val: &'a RustMmsValue,
        _test: bool,
    ) -> RustOperateFuture<'a> {
        let path = &self.user_path;
        let callback = &self.callback;
        let locals = &self.task_locals;
        Box::pin(async move {
            let step = invoke_control_callable(callback, locals, |py| {
                let action_dict = control_action_to_pydict(py, action)?;
                let ctl_val_py = mms_value_to_pyobject(py, ctl_val)?;
                pyo3::types::PyTuple::new(
                    py,
                    [
                        path.into_pyobject(py)?.into_any(),
                        ctl_val_py.into_bound(py),
                        action_dict.into_any(),
                    ],
                )
            });
            finish_handler_step(step).await
        })
    }
}

struct PyControlWaitHandler {
    user_path: String,
    callback: Py<PyAny>,
    task_locals: TaskLocals,
}

impl RustWaitHandler for PyControlWaitHandler {
    fn wait_for_execution<'a>(
        &'a self,
        action: &'a RustControlAction,
        ctl_val: &'a RustMmsValue,
        _test: bool,
        _synchro_check: bool,
    ) -> RustWaitForExecFuture<'a> {
        let path = &self.user_path;
        let callback = &self.callback;
        let locals = &self.task_locals;
        Box::pin(async move {
            let step = invoke_control_callable(callback, locals, |py| {
                let action_dict = control_action_to_pydict(py, action)?;
                let ctl_val_py = mms_value_to_pyobject(py, ctl_val)?;
                pyo3::types::PyTuple::new(
                    py,
                    [
                        path.into_pyobject(py)?.into_any(),
                        ctl_val_py.into_bound(py),
                        action_dict.into_any(),
                    ],
                )
            });
            finish_handler_step(step).await
        })
    }
}

fn control_model_from_str(s: &str) -> PyResult<RustServerControlModel> {
    match s {
        "status-only" => Ok(RustServerControlModel::StatusOnly),
        "direct-normal" => Ok(RustServerControlModel::DirectNormal),
        "sbo-normal" => Ok(RustServerControlModel::SboNormal),
        "direct-enhanced" => Ok(RustServerControlModel::DirectEnhanced),
        "sbo-enhanced" => Ok(RustServerControlModel::SboEnhanced),
        _ => Err(PyValueError::new_err(format!(
            "ctl_model must be one of \
             'status-only' / 'direct-normal' / 'sbo-normal' / \
             'direct-enhanced' / 'sbo-enhanced', got '{s}'"
        ))),
    }
}

fn sbo_class_from_str(s: &str) -> PyResult<RustServerSboClass> {
    match s {
        "operate-once" => Ok(RustServerSboClass::OperateOnce),
        "operate-many" => Ok(RustServerSboClass::OperateMany),
        _ => Err(PyValueError::new_err(format!(
            "sbo_class must be 'operate-once' or 'operate-many', got '{s}'"
        ))),
    }
}

fn build_control_entry(pc: PendingControl, task_locals: TaskLocals) -> RustControlObjectEntry {
    let obj = RustControlObject::new(pc.config);
    let mut entry = RustControlObjectEntry::new(obj);
    if let Some(cb) = pc.check {
        entry = entry.with_check(Arc::new(PyCheckHandler {
            user_path: pc.user_path.clone(),
            callback: cb,
        }) as Arc<dyn RustCheckHandler>);
    }
    if let Some(cb) = pc.operate {
        entry = entry.with_operate(Arc::new(PyControlOperateHandler {
            user_path: pc.user_path.clone(),
            callback: cb,
            task_locals: task_locals.clone(),
        }) as Arc<dyn RustControlHandler>);
    }
    if let Some(cb) = pc.wait {
        entry = entry.with_wait(Arc::new(PyControlWaitHandler {
            user_path: pc.user_path,
            callback: cb,
            task_locals,
        }) as Arc<dyn RustWaitHandler>);
    }
    entry
}

// ─────────────────────────────────────────────────────────────────────────────
// Reporting helpers (Dataset / URCB)
// ─────────────────────────────────────────────────────────────────────────────

fn parse_trg_ops(items: &[String]) -> PyResult<RustServerTriggerOptions> {
    let mut acc = RustServerTriggerOptions::NONE;
    for item in items {
        let flag = match item.as_str() {
            "none" => RustServerTriggerOptions::NONE,
            "data_changed" => RustServerTriggerOptions::DATA_CHANGED,
            "quality_changed" => RustServerTriggerOptions::QUALITY_CHANGED,
            "data_update" => RustServerTriggerOptions::DATA_UPDATE,
            "integrity" => RustServerTriggerOptions::INTEGRITY,
            "gi" => RustServerTriggerOptions::GI,
            "all" => RustServerTriggerOptions::ALL,
            other => {
                return Err(PyValueError::new_err(format!(
                    "trg_ops: unknown flag '{other}' (allowed: data_changed, \
                     quality_changed, data_update, integrity, gi, all, none)"
                )));
            }
        };
        acc |= flag;
    }
    Ok(acc)
}

fn parse_opt_flds(items: &[String]) -> PyResult<RustServerOptFlds> {
    let mut acc = RustServerOptFlds::NONE;
    for item in items {
        let flag = match item.as_str() {
            "seq_num" => RustServerOptFlds::SEQ_NUM,
            "time_stamp" => RustServerOptFlds::TIME_STAMP,
            "reason" => RustServerOptFlds::REASON,
            "data_set" => RustServerOptFlds::DATA_SET,
            "data_reference" => RustServerOptFlds::DATA_REFERENCE,
            "buffer_overflow" => RustServerOptFlds::BUFFER_OVERFLOW,
            "entry_id" => RustServerOptFlds::ENTRY_ID,
            "conf_rev" => RustServerOptFlds::CONF_REV,
            other => {
                return Err(PyValueError::new_err(format!(
                    "opt_flds: unknown flag '{other}' (allowed: seq_num, \
                     time_stamp, reason, data_set, data_reference, conf_rev, \
                     buffer_overflow, entry_id)"
                )));
            }
        };
        acc |= flag;
    }
    Ok(acc)
}

fn build_dataset(pd: &PendingDataset) -> RustDataset {
    let mut ds = RustDataset::new(&pd.name);
    for entry in &pd.entries {
        ds.push(entry.clone());
    }
    ds
}

fn build_rcb(urcb: &PendingUrcb) -> RustRcb {
    RustRcb::new(urcb.rcb_name.as_str(), urcb.dataset_name.as_str())
        .with_rpt_id(urcb.rpt_id.as_str())
        .with_conf_rev(urcb.conf_rev)
        .with_trg_ops(urcb.trg_ops)
        .with_opt_flds(urcb.opt_flds)
        .with_buf_tm_ms(urcb.buf_tm_ms)
        .with_intg_pd_ms(urcb.intg_pd_ms)
}

fn build_brcb(brcb: &PendingBrcb) -> RustBrcb {
    let mut b = RustBrcb::new(brcb.rcb_name.as_str(), brcb.dataset_name.as_str())
        .with_rpt_id(brcb.rpt_id.as_str())
        .with_conf_rev(brcb.conf_rev)
        .with_trg_ops(brcb.trg_ops)
        .with_opt_flds(brcb.opt_flds)
        .with_buf_tm_ms(brcb.buf_tm_ms)
        .with_intg_pd_ms(brcb.intg_pd_ms)
        .with_buffer_capacity(brcb.buffer_capacity);
    b.with_resv_tms = brcb.with_resv_tms;
    b.with_owner = brcb.with_owner;
    b
}

/// Materialise a queued `PendingLogControl` into a runtime `LogControl`.
///
/// Allocates a fresh `InMemoryLogStorage` (with optional capacity bound),
/// applies the LCB configuration, and flips `LogEna` on when `default_enabled`
/// is set — `set_log_ena(true)` is the only valid path to enable, since the
/// `LogControlBlock::default_enabled` flag alone does not seed the runtime
/// state to `Enabled`.
fn build_log_control(plcb: &PendingLogControl) -> PyResult<RustLogControl> {
    let storage: Arc<dyn RustLogStorage> = match plcb.storage_capacity {
        Some(cap) => Arc::new(RustInMemoryLogStorage::with_capacity(cap)),
        None => Arc::new(RustInMemoryLogStorage::new()),
    };
    let mut lcb_block = RustLogControlBlock::new(plcb.lcb_name.clone())
        .with_dataset(plcb.dataset_name.clone())
        .with_trg_ops(plcb.trg_ops)
        .with_intg_pd_ms(plcb.intg_period_ms);
    if let Some(lr) = &plcb.log_ref {
        lcb_block = lcb_block.with_log_ref(lr.clone());
    }
    lcb_block.default_enabled = plcb.default_enabled;
    lcb_block.include_reason_code = plcb.include_reason_code;
    let lc = RustLogControl::new(plcb.mms_path.clone(), lcb_block).with_storage(storage);
    if plcb.default_enabled {
        lc.set_log_ena(true).map_err(|e| {
            PyRuntimeError::new_err(format!(
                "start: LCB '{}' set_log_ena failed: {e}",
                plcb.mms_path
            ))
        })?;
    }
    Ok(lc)
}

// ─────────────────────────────────────────────────────────────────────────────
// Direct IedModel construction from a Python dict (no SCL round-trip).
//
// Mirrors the IedModelBuilder fluent API behind a single declarative payload:
// callers pass one dict shaped like an SCL skeleton, the walker validates and
// invokes the underlying builders. RCB / LCB / SGCB declared here land in the
// model so the server picks them up at `start()` exactly as it would for SCL.
// ─────────────────────────────────────────────────────────────────────────────

/// Build a `RustIedModel` from a `dict`-style spec. The accepted shape is
/// documented on `PyIedServer::from_model_spec`.
fn build_model_from_spec(py: Python<'_>, spec: &Bound<'_, PyDict>) -> PyResult<RustIedModel> {
    let ied_name: String = get_required_string(spec, "ied_name", "from_model_spec")?;
    let lds_any = spec
        .get_item("lds")?
        .ok_or_else(|| PyValueError::new_err("from_model_spec: missing required key 'lds'"))?;
    let lds = lds_any
        .cast::<PyList>()
        .map_err(|_| PyValueError::new_err("from_model_spec: 'lds' must be a list"))?;
    if lds.is_empty() {
        return Err(PyValueError::new_err(
            "from_model_spec: at least one logical device is required",
        ));
    }

    let mut model_builder = RustIedModelBuilder::new(ied_name);
    for (i, ld_any) in lds.iter().enumerate() {
        let ld_dict = ld_any.cast::<PyDict>().map_err(|_| {
            PyValueError::new_err(format!("from_model_spec: lds[{i}] must be a dict"))
        })?;
        let ld = build_ld_from_spec(py, ld_dict, i)?;
        model_builder = model_builder
            .add_ld(ld)
            .map_err(|e| map_model_error("from_model_spec", e))?;
    }
    model_builder
        .build()
        .map_err(|e| map_model_error("from_model_spec", e))
}

fn build_ld_from_spec(
    py: Python<'_>,
    ld: &Bound<'_, PyDict>,
    idx: usize,
) -> PyResult<iec61850_model::tree::LogicalDevice> {
    let inst = get_required_string(ld, "inst", &format!("lds[{idx}]"))?;
    let mut b = RustLdBuilder::new(inst);
    if let Some(name_any) = ld.get_item("ld_name")? {
        if !name_any.is_none() {
            let name: String = name_any.extract().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: lds[{idx}].ld_name must be a string"
                ))
            })?;
            b = b.with_ld_name(name);
        }
    }
    let lns_any = ld.get_item("lns")?.ok_or_else(|| {
        PyValueError::new_err(format!(
            "from_model_spec: lds[{idx}] missing required key 'lns'"
        ))
    })?;
    let lns = lns_any.cast::<PyList>().map_err(|_| {
        PyValueError::new_err(format!("from_model_spec: lds[{idx}].lns must be a list"))
    })?;
    if lns.is_empty() {
        return Err(PyValueError::new_err(format!(
            "from_model_spec: lds[{idx}].lns must contain at least an LLN0"
        )));
    }
    for (j, ln_any) in lns.iter().enumerate() {
        let ln_dict = ln_any.cast::<PyDict>().map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: lds[{idx}].lns[{j}] must be a dict"
            ))
        })?;
        let ln = build_ln_from_spec(py, ln_dict, idx, j)?;
        b = b.add_ln(ln);
    }
    b.build().map_err(|e| map_model_error("from_model_spec", e))
}

fn build_ln_from_spec(
    py: Python<'_>,
    ln: &Bound<'_, PyDict>,
    ld_idx: usize,
    ln_idx: usize,
) -> PyResult<iec61850_model::tree::LogicalNode> {
    // `lln0=True` is a convenience: equivalent to class="LLN0", prefix="", inst="".
    let is_lln0 = get_optional_bool(ln, "lln0")?.unwrap_or(false);
    let (prefix, class, inst) = if is_lln0 {
        (String::new(), "LLN0".to_string(), String::new())
    } else {
        (
            get_optional_string(ln, "prefix")?.unwrap_or_default(),
            get_required_string(ln, "class", &format!("lds[{ld_idx}].lns[{ln_idx}]"))?,
            get_optional_string(ln, "inst")?.unwrap_or_default(),
        )
    };
    let mut b = RustLnBuilder::new(prefix, class, inst);

    if let Some(dos_any) = ln.get_item("dos")? {
        if !dos_any.is_none() {
            let dos = dos_any.cast::<PyList>().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: lds[{ld_idx}].lns[{ln_idx}].dos must be a list"
                ))
            })?;
            for (k, do_any) in dos.iter().enumerate() {
                let do_dict = do_any.cast::<PyDict>().map_err(|_| {
                    PyValueError::new_err(format!(
                        "from_model_spec: lds[{ld_idx}].lns[{ln_idx}].dos[{k}] must be a dict"
                    ))
                })?;
                let d = build_do_from_spec(py, do_dict, ld_idx, ln_idx, k)?;
                b = b.add_do(d);
            }
        }
    }

    if let Some(ds_any) = ln.get_item("datasets")? {
        if !ds_any.is_none() {
            let datasets = ds_any.cast::<PyList>().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: lds[{ld_idx}].lns[{ln_idx}].datasets must be a list"
                ))
            })?;
            for (k, ds_item) in datasets.iter().enumerate() {
                let ds_dict = ds_item.cast::<PyDict>().map_err(|_| {
                    PyValueError::new_err(format!(
                        "from_model_spec: lds[{ld_idx}].lns[{ln_idx}].datasets[{k}] must be a dict"
                    ))
                })?;
                b = b.add_dataset(build_dataset_from_spec(ds_dict, ld_idx, ln_idx, k)?);
            }
        }
    }

    if let Some(rcbs_any) = ln.get_item("rcbs")? {
        if !rcbs_any.is_none() {
            let rcbs = rcbs_any.cast::<PyList>().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: lds[{ld_idx}].lns[{ln_idx}].rcbs must be a list"
                ))
            })?;
            for (k, rcb_item) in rcbs.iter().enumerate() {
                let rcb_dict = rcb_item.cast::<PyDict>().map_err(|_| {
                    PyValueError::new_err(format!(
                        "from_model_spec: lds[{ld_idx}].lns[{ln_idx}].rcbs[{k}] must be a dict"
                    ))
                })?;
                b = b.add_rcb(build_rcb_from_spec(rcb_dict, ld_idx, ln_idx, k)?);
            }
        }
    }

    if let Some(lcbs_any) = ln.get_item("lcbs")? {
        if !lcbs_any.is_none() {
            let lcbs = lcbs_any.cast::<PyList>().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: lds[{ld_idx}].lns[{ln_idx}].lcbs must be a list"
                ))
            })?;
            for (k, lcb_item) in lcbs.iter().enumerate() {
                let lcb_dict = lcb_item.cast::<PyDict>().map_err(|_| {
                    PyValueError::new_err(format!(
                        "from_model_spec: lds[{ld_idx}].lns[{ln_idx}].lcbs[{k}] must be a dict"
                    ))
                })?;
                b = b.add_lcb(build_lcb_from_spec(lcb_dict, ld_idx, ln_idx, k)?);
            }
        }
    }

    if let Some(sgcb_any) = ln.get_item("sgcb")? {
        if !sgcb_any.is_none() {
            let sgcb_dict = sgcb_any.cast::<PyDict>().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: lds[{ld_idx}].lns[{ln_idx}].sgcb must be a dict"
                ))
            })?;
            b = b.set_sgcb(build_sgcb_from_spec(sgcb_dict, ld_idx, ln_idx)?);
        }
    }

    b.build().map_err(|e| map_model_error("from_model_spec", e))
}

fn build_do_from_spec(
    py: Python<'_>,
    do_dict: &Bound<'_, PyDict>,
    ld_idx: usize,
    ln_idx: usize,
    do_idx: usize,
) -> PyResult<iec61850_model::tree::DataObject> {
    let ctx = format!("lds[{ld_idx}].lns[{ln_idx}].dos[{do_idx}]");
    let name = get_required_string(do_dict, "name", &ctx)?;
    let array_count = match do_dict.get_item("array_count")? {
        Some(any) if !any.is_none() => Some(any.extract::<u32>().map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: {ctx}.array_count must be a non-negative integer"
            ))
        })?),
        _ => None,
    };
    let mut b = match array_count {
        Some(n) => RustDoBuilder::array(name, n),
        None => RustDoBuilder::scalar(name),
    };

    if let Some(das_any) = do_dict.get_item("das")? {
        if !das_any.is_none() {
            let das = das_any.cast::<PyList>().map_err(|_| {
                PyValueError::new_err(format!("from_model_spec: {ctx}.das must be a list"))
            })?;
            for (k, da_item) in das.iter().enumerate() {
                let da_dict = da_item.cast::<PyDict>().map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.das[{k}] must be a dict"))
                })?;
                let inner_ctx = format!("{ctx}.das[{k}]");
                let da_name = get_required_string(da_dict, "name", &inner_ctx)?;
                let fc_token = get_required_string(da_dict, "fc", &inner_ctx)?;
                let fc = parse_fc(&fc_token)?;
                let ty_any = da_dict.get_item("type")?.ok_or_else(|| {
                    PyValueError::new_err(format!(
                        "from_model_spec: {inner_ctx} missing required key 'type'"
                    ))
                })?;
                let ty = parse_da_type(&ty_any, &inner_ctx)?;
                let trg_ops = match da_dict.get_item("trg_ops")? {
                    Some(v) if !v.is_none() => {
                        let items: Vec<String> = v.extract().map_err(|_| {
                            PyValueError::new_err(format!(
                                "from_model_spec: {inner_ctx}.trg_ops must be a list of strings"
                            ))
                        })?;
                        parse_model_trg_ops(&items, &inner_ctx)?
                    }
                    _ => RustModelTrgOps::NONE,
                };
                let value = match da_dict.get_item("value")? {
                    Some(v) if !v.is_none() => parse_mms_value(py, &v, ty, &inner_ctx)?,
                    _ => RustMmsValue::default_for(ty),
                };
                b = b.add_da(da_name, fc, ty, trg_ops, value);
            }
        }
    }

    if let Some(constructed_any) = do_dict.get_item("constructed_das")? {
        if !constructed_any.is_none() {
            let list = constructed_any.cast::<PyList>().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: {ctx}.constructed_das must be a list"
                ))
            })?;
            for (k, item) in list.iter().enumerate() {
                let cd_dict = item.cast::<PyDict>().map_err(|_| {
                    PyValueError::new_err(format!(
                        "from_model_spec: {ctx}.constructed_das[{k}] must be a dict"
                    ))
                })?;
                let inner_ctx = format!("{ctx}.constructed_das[{k}]");
                b = b.add_da_node(build_constructed_da_from_spec(py, cd_dict, &inner_ctx)?);
            }
        }
    }

    if let Some(sub_any) = do_dict.get_item("sub_dos")? {
        if !sub_any.is_none() {
            let list = sub_any.cast::<PyList>().map_err(|_| {
                PyValueError::new_err(format!("from_model_spec: {ctx}.sub_dos must be a list"))
            })?;
            for (k, item) in list.iter().enumerate() {
                let sd_dict = item.cast::<PyDict>().map_err(|_| {
                    PyValueError::new_err(format!(
                        "from_model_spec: {ctx}.sub_dos[{k}] must be a dict"
                    ))
                })?;
                b = b.add_sub_do(build_do_from_spec(py, sd_dict, ld_idx, ln_idx, k)?);
            }
        }
    }

    b.build().map_err(|e| map_model_error("from_model_spec", e))
}

fn build_constructed_da_from_spec(
    py: Python<'_>,
    da: &Bound<'_, PyDict>,
    ctx: &str,
) -> PyResult<RustDataAttribute> {
    let name = get_required_string(da, "name", ctx)?;
    let fc_token = get_required_string(da, "fc", ctx)?;
    let fc = parse_fc(&fc_token)?;
    let children_any = da.get_item("children")?.ok_or_else(|| {
        PyValueError::new_err(format!(
            "from_model_spec: {ctx}.children is required (constructed DA must have >=1 child)"
        ))
    })?;
    let list = children_any.cast::<PyList>().map_err(|_| {
        PyValueError::new_err(format!("from_model_spec: {ctx}.children must be a list"))
    })?;
    if list.is_empty() {
        return Err(PyValueError::new_err(format!(
            "from_model_spec: {ctx}.children must contain at least one DA"
        )));
    }
    let mut children = Vec::with_capacity(list.len());
    for (k, item) in list.iter().enumerate() {
        let child_dict = item.cast::<PyDict>().map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: {ctx}.children[{k}] must be a dict"
            ))
        })?;
        let child_ctx = format!("{ctx}.children[{k}]");
        let cname = get_required_string(child_dict, "name", &child_ctx)?;
        let cfc_token = get_required_string(child_dict, "fc", &child_ctx)?;
        let cfc = parse_fc(&cfc_token)?;
        let cty_any = child_dict.get_item("type")?.ok_or_else(|| {
            PyValueError::new_err(format!(
                "from_model_spec: {child_ctx} missing required key 'type'"
            ))
        })?;
        let cty = parse_da_type(&cty_any, &child_ctx)?;
        let ctrg = match child_dict.get_item("trg_ops")? {
            Some(v) if !v.is_none() => {
                let items: Vec<String> = v.extract().map_err(|_| {
                    PyValueError::new_err(format!(
                        "from_model_spec: {child_ctx}.trg_ops must be a list of strings"
                    ))
                })?;
                parse_model_trg_ops(&items, &child_ctx)?
            }
            _ => RustModelTrgOps::NONE,
        };
        let cval = match child_dict.get_item("value")? {
            Some(v) if !v.is_none() => parse_mms_value(py, &v, cty, &child_ctx)?,
            _ => RustMmsValue::default_for(cty),
        };
        children.push(RustDataAttribute::new(cname, cfc, cty, ctrg, cval));
    }
    Ok(RustDataAttribute::constructed(name, fc, children))
}

fn build_dataset_from_spec(
    ds: &Bound<'_, PyDict>,
    ld_idx: usize,
    ln_idx: usize,
    ds_idx: usize,
) -> PyResult<RustModelDataSet> {
    let ctx = format!("lds[{ld_idx}].lns[{ln_idx}].datasets[{ds_idx}]");
    let name = get_required_string(ds, "name", &ctx)?;
    let entries_any = ds.get_item("entries")?.ok_or_else(|| {
        PyValueError::new_err(format!(
            "from_model_spec: {ctx} missing required key 'entries'"
        ))
    })?;
    let list = entries_any.cast::<PyList>().map_err(|_| {
        PyValueError::new_err(format!("from_model_spec: {ctx}.entries must be a list"))
    })?;
    let mut entries = Vec::with_capacity(list.len());
    for (k, item) in list.iter().enumerate() {
        let e = item.cast::<PyDict>().map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: {ctx}.entries[{k}] must be a dict"
            ))
        })?;
        let ectx = format!("{ctx}.entries[{k}]");
        let ld_inst = get_optional_string(e, "ld_inst")?.unwrap_or_default();
        let ln_name = get_required_string(e, "ln_name", &ectx)?;
        let fc_token = get_required_string(e, "fc", &ectx)?;
        let fc = parse_fc(&fc_token)?;
        let do_path_any = e.get_item("do_path")?.ok_or_else(|| {
            PyValueError::new_err(format!(
                "from_model_spec: {ectx} missing required key 'do_path'"
            ))
        })?;
        let do_path: Vec<String> = do_path_any.extract().map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: {ectx}.do_path must be a list of strings"
            ))
        })?;
        if do_path.is_empty() {
            return Err(PyValueError::new_err(format!(
                "from_model_spec: {ectx}.do_path must not be empty"
            )));
        }
        let array_index = match e.get_item("array_index")? {
            Some(v) if !v.is_none() => Some(v.extract::<u32>().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: {ectx}.array_index must be a non-negative integer"
                ))
            })?),
            _ => None,
        };
        let component = get_optional_string(e, "component")?;
        entries.push(RustModelDataSetEntry {
            ld_inst,
            ln_name,
            fc,
            do_path,
            array_index,
            component,
        });
    }
    Ok(RustModelDataSet { name, entries })
}

fn build_rcb_from_spec(
    rcb: &Bound<'_, PyDict>,
    ld_idx: usize,
    ln_idx: usize,
    rcb_idx: usize,
) -> PyResult<RustModelRcb> {
    let ctx = format!("lds[{ld_idx}].lns[{ln_idx}].rcbs[{rcb_idx}]");
    let name = get_required_string(rcb, "name", &ctx)?;
    let is_buffered = get_optional_bool(rcb, "buffered")?.unwrap_or(false);
    let dataset_ref = get_required_string(rcb, "dataset_ref", &ctx)?;
    let conf_rev = get_optional_u32(rcb, "conf_rev")?.unwrap_or(1);
    let rpt_id = get_optional_string(rcb, "rpt_id")?.unwrap_or_default();
    let trg_ops = match rcb.get_item("trg_ops")? {
        Some(v) if !v.is_none() => {
            let items: Vec<String> = v.extract().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: {ctx}.trg_ops must be a list of strings"
                ))
            })?;
            parse_model_trg_ops(&items, &ctx)?
        }
        _ => RustModelTrgOps::NONE,
    };
    let opt_flds = match rcb.get_item("opt_flds")? {
        Some(v) if !v.is_none() => {
            let items: Vec<String> = v.extract().map_err(|_| {
                PyValueError::new_err(format!(
                    "from_model_spec: {ctx}.opt_flds must be a list of strings"
                ))
            })?;
            parse_model_opt_flds(&items, &ctx)?
        }
        _ => RustModelOptFlds::NONE,
    };
    let buf_tm_ms = get_optional_u32(rcb, "buf_tm_ms")?.unwrap_or(0);
    let intg_pd_ms = get_optional_u32(rcb, "intg_pd_ms")?.unwrap_or(0);
    Ok(RustModelRcb {
        name,
        is_buffered,
        dataset_ref,
        conf_rev,
        rpt_id,
        trg_ops,
        opt_flds,
        buf_tm_ms,
        intg_pd_ms,
    })
}

fn build_lcb_from_spec(
    lcb: &Bound<'_, PyDict>,
    ld_idx: usize,
    ln_idx: usize,
    lcb_idx: usize,
) -> PyResult<RustModelLcb> {
    let ctx = format!("lds[{ld_idx}].lns[{ln_idx}].lcbs[{lcb_idx}]");
    Ok(RustModelLcb {
        name: get_required_string(lcb, "name", &ctx)?,
        dataset_ref: get_required_string(lcb, "dataset_ref", &ctx)?,
        log_ref: get_optional_string(lcb, "log_ref")?.unwrap_or_default(),
    })
}

fn build_sgcb_from_spec(
    sgcb: &Bound<'_, PyDict>,
    ld_idx: usize,
    ln_idx: usize,
) -> PyResult<RustModelSgcb> {
    let ctx = format!("lds[{ld_idx}].lns[{ln_idx}].sgcb");
    let num_of_sg = sgcb
        .get_item("num_of_sg")?
        .ok_or_else(|| {
            PyValueError::new_err(format!(
                "from_model_spec: {ctx} missing required key 'num_of_sg'"
            ))
        })?
        .extract::<u8>()
        .map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: {ctx}.num_of_sg must be an unsigned integer (1..=255)"
            ))
        })?;
    let act_sg = get_optional_u8(sgcb, "act_sg")?.unwrap_or(1);
    let has_resv_tms = get_optional_bool(sgcb, "has_resv_tms")?.unwrap_or(false);
    let default_resv_tms_s = get_optional_u32(sgcb, "default_resv_tms_s")?
        .map(|n| n.min(u16::MAX as u32) as u16)
        .unwrap_or(60);
    Ok(RustModelSgcb {
        num_of_sg,
        act_sg,
        has_resv_tms,
        default_resv_tms_s,
    })
}

fn parse_da_type(type_any: &Bound<'_, PyAny>, ctx: &str) -> PyResult<RustDataAttributeType> {
    if let Ok(s) = type_any.extract::<String>() {
        return da_type_from_token(&s, None, ctx);
    }
    let d = type_any.cast::<PyDict>().map_err(|_| {
        PyValueError::new_err(format!(
            "from_model_spec: {ctx}.type must be a string or {{'type': str, 'max_len': int}} dict"
        ))
    })?;
    let kind = get_required_string(d, "type", ctx)?;
    let max_len: Option<u16> =
        get_optional_u32(d, "max_len")?.map(|n| n.min(u16::MAX as u32) as u16);
    da_type_from_token(&kind, max_len, ctx)
}

fn da_type_from_token(
    token: &str,
    max_len: Option<u16>,
    ctx: &str,
) -> PyResult<RustDataAttributeType> {
    use RustDataAttributeType as T;
    Ok(match token {
        "Boolean" => T::Boolean,
        "Int8" => T::Int8,
        "Int16" => T::Int16,
        "Int32" => T::Int32,
        "Int64" => T::Int64,
        "Int128" => T::Int128,
        "Int8U" => T::Int8U,
        "Int16U" => T::Int16U,
        "Int24U" => T::Int24U,
        "Int32U" => T::Int32U,
        "Float32" => T::Float32,
        "Float64" => T::Float64,
        "Enumerated" => T::Enumerated,
        "OctetString" => T::OctetString(max_len.unwrap_or(64)),
        "VisibleString" => T::VisibleString(max_len.unwrap_or(64)),
        "UnicodeString255" => T::UnicodeString255,
        "Timestamp" => T::Timestamp,
        "Quality" => T::Quality,
        "Check" => T::Check,
        "CodedEnum" => T::CodedEnum,
        "GenericBitString" => T::GenericBitString(max_len.unwrap_or(8)),
        "Constructed" => T::Constructed,
        "EntryTime" => T::EntryTime,
        "PhyComAddr" => T::PhyComAddr,
        "Currency" => T::Currency,
        "OptFlds" => T::OptFlds,
        "TrgOpsBits" => T::TrgOpsBits,
        other => {
            return Err(PyValueError::new_err(format!(
                "from_model_spec: {ctx}.type '{other}' is not a known DataAttributeType"
            )));
        }
    })
}

fn parse_mms_value(
    _py: Python<'_>,
    val: &Bound<'_, PyAny>,
    ty: RustDataAttributeType,
    ctx: &str,
) -> PyResult<RustMmsValue> {
    let d = val.cast::<PyDict>().map_err(|_| {
        PyValueError::new_err(format!(
            "from_model_spec: {ctx}.value must be a dict like {{'type': 'bool', 'value': ...}}"
        ))
    })?;
    let kind = get_required_string(d, "type", ctx)?;
    if kind == "default" {
        return Ok(RustMmsValue::default_for(ty));
    }
    let inner_any = d.get_item("value")?;
    let inner = inner_any.as_ref();
    match kind.as_str() {
        "bool" => Ok(RustMmsValue::Boolean(
            inner
                .ok_or_else(|| missing_value(ctx, "bool"))?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.value must be a bool"))
                })?,
        )),
        "int" => Ok(RustMmsValue::Integer(
            inner
                .ok_or_else(|| missing_value(ctx, "int"))?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.value must be an i64"))
                })?,
        )),
        "uint" => Ok(RustMmsValue::Unsigned(
            inner
                .ok_or_else(|| missing_value(ctx, "uint"))?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.value must be a u64"))
                })?,
        )),
        "float32" => Ok(RustMmsValue::Float32(
            inner
                .ok_or_else(|| missing_value(ctx, "float32"))?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.value must be a float"))
                })?,
        )),
        "float64" => Ok(RustMmsValue::Float64(
            inner
                .ok_or_else(|| missing_value(ctx, "float64"))?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.value must be a float"))
                })?,
        )),
        "visible_string" => Ok(RustMmsValue::VisibleString(
            inner
                .ok_or_else(|| missing_value(ctx, "visible_string"))?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.value must be a string"))
                })?,
        )),
        "mms_string" => Ok(RustMmsValue::MmsString(
            inner
                .ok_or_else(|| missing_value(ctx, "mms_string"))?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.value must be a string"))
                })?,
        )),
        "octet_string" => Ok(RustMmsValue::OctetString(
            inner
                .ok_or_else(|| missing_value(ctx, "octet_string"))?
                .extract::<Vec<u8>>()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.value must be bytes"))
                })?,
        )),
        "utc_time" => {
            let bytes: Vec<u8> = inner
                .ok_or_else(|| missing_value(ctx, "utc_time"))?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.value must be 8 bytes"))
                })?;
            if bytes.len() != 8 {
                return Err(PyValueError::new_err(format!(
                    "from_model_spec: {ctx}.value for utc_time must be exactly 8 bytes (got {})",
                    bytes.len()
                )));
            }
            let mut arr = [0u8; 8];
            arr.copy_from_slice(&bytes);
            Ok(RustMmsValue::UtcTime(arr))
        }
        "binary_time" => {
            let bytes: Vec<u8> = inner
                .ok_or_else(|| missing_value(ctx, "binary_time"))?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!(
                        "from_model_spec: {ctx}.value must be 4 or 6 bytes"
                    ))
                })?;
            if bytes.len() != 4 && bytes.len() != 6 {
                return Err(PyValueError::new_err(format!(
                    "from_model_spec: {ctx}.value for binary_time must be 4 or 6 bytes (got {})",
                    bytes.len()
                )));
            }
            Ok(RustMmsValue::BinaryTime(bytes))
        }
        "bit_string" => {
            let padding = d
                .get_item("padding")?
                .ok_or_else(|| {
                    PyValueError::new_err(format!(
                        "from_model_spec: {ctx} bit_string requires 'padding' key"
                    ))
                })?
                .extract::<u8>()
                .map_err(|_| {
                    PyValueError::new_err(format!(
                        "from_model_spec: {ctx}.padding must be a u8 (0..=7)"
                    ))
                })?;
            let data: Vec<u8> = d
                .get_item("data")?
                .ok_or_else(|| {
                    PyValueError::new_err(format!(
                        "from_model_spec: {ctx} bit_string requires 'data' key"
                    ))
                })?
                .extract()
                .map_err(|_| {
                    PyValueError::new_err(format!("from_model_spec: {ctx}.data must be bytes"))
                })?;
            Ok(RustMmsValue::BitString { padding, data })
        }
        other => Err(PyValueError::new_err(format!(
            "from_model_spec: {ctx}.value type '{other}' is not supported \
             (allowed: bool, int, uint, float32, float64, visible_string, mms_string, \
             octet_string, utc_time, binary_time, bit_string, default)"
        ))),
    }
}

fn missing_value(ctx: &str, kind: &str) -> PyErr {
    PyValueError::new_err(format!(
        "from_model_spec: {ctx} value of type '{kind}' requires a 'value' key"
    ))
}

fn parse_model_trg_ops(items: &[String], ctx: &str) -> PyResult<RustModelTrgOps> {
    let mut acc = RustModelTrgOps::NONE;
    for item in items {
        let flag = match item.as_str() {
            "none" => RustModelTrgOps::NONE,
            "data_changed" => RustModelTrgOps::DCHG,
            "quality_changed" => RustModelTrgOps::QCHG,
            "data_update" => RustModelTrgOps::DUPD,
            "integrity" => RustModelTrgOps::INTEGRITY,
            "gi" => RustModelTrgOps::GI,
            "all" => RustModelTrgOps::ALL,
            other => {
                return Err(PyValueError::new_err(format!(
                    "from_model_spec: {ctx}.trg_ops unknown flag '{other}' \
                     (allowed: data_changed, quality_changed, data_update, integrity, gi, all, none)"
                )));
            }
        };
        acc = acc.union(flag);
    }
    Ok(acc)
}

fn parse_model_opt_flds(items: &[String], ctx: &str) -> PyResult<RustModelOptFlds> {
    let mut acc = RustModelOptFlds::NONE;
    for item in items {
        let flag = match item.as_str() {
            "seq_num" => RustModelOptFlds::SEQ_NUM,
            "time_stamp" => RustModelOptFlds::TIME_STAMP,
            "reason" => RustModelOptFlds::REASON,
            "data_set" => RustModelOptFlds::DATA_SET,
            "data_reference" => RustModelOptFlds::DATA_REFERENCE,
            "buffer_overflow" => RustModelOptFlds::BUFFER_OVERFLOW,
            "entry_id" => RustModelOptFlds::ENTRY_ID,
            "conf_rev" => RustModelOptFlds::CONF_REV,
            "segmentation" => RustModelOptFlds::SEGMENTATION,
            other => {
                return Err(PyValueError::new_err(format!(
                    "from_model_spec: {ctx}.opt_flds unknown flag '{other}' \
                     (allowed: seq_num, time_stamp, reason, data_set, data_reference, \
                     buffer_overflow, entry_id, conf_rev, segmentation)"
                )));
            }
        };
        acc |= flag;
    }
    Ok(acc)
}

fn map_model_error(caller: &str, err: iec61850_model::error::ModelError) -> PyErr {
    PyValueError::new_err(format!("{caller}: {err}"))
}

fn get_required_string(d: &Bound<'_, PyDict>, key: &str, ctx: &str) -> PyResult<String> {
    let any = d.get_item(key)?.ok_or_else(|| {
        PyValueError::new_err(format!(
            "from_model_spec: {ctx} missing required key '{key}'"
        ))
    })?;
    any.extract::<String>().map_err(|_| {
        PyValueError::new_err(format!("from_model_spec: {ctx}.{key} must be a string"))
    })
}

fn get_optional_string(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<String>> {
    match d.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v.extract::<String>().map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: optional key '{key}' must be a string when set"
            ))
        })?)),
        _ => Ok(None),
    }
}

fn get_optional_bool(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<bool>> {
    match d.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v.extract::<bool>().map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: optional key '{key}' must be a bool when set"
            ))
        })?)),
        _ => Ok(None),
    }
}

fn get_optional_u32(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<u32>> {
    match d.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v.extract::<u32>().map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: optional key '{key}' must be a non-negative u32 when set"
            ))
        })?)),
        _ => Ok(None),
    }
}

fn get_optional_u8(d: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<u8>> {
    match d.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v.extract::<u8>().map_err(|_| {
            PyValueError::new_err(format!(
                "from_model_spec: optional key '{key}' must be a u8 (0..=255) when set"
            ))
        })?)),
        _ => Ok(None),
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// BatchGuard — RAII context manager around `IedServer::lock_data_model`.
// ─────────────────────────────────────────────────────────────────────────────

struct BatchGuardInner {
    // Drop order matters: `_guard` must drop before `_server` so the
    // mutex borrow stays valid until release.
    _guard: RustDataModelGuard<'static>,
    _server: Arc<RustIedServer>,
}

/// Synchronous RAII guard returned by `IedServer.batch()`. Acquires the
/// server-side data-model lock on construction; releases it on `__exit__`
/// (or when the object is dropped). Re-entry raises `RuntimeError`.
#[pyclass(name = "BatchGuard", module = "iec61850._native", unsendable)]
struct PyBatchGuard {
    inner: StdRwLock<Option<BatchGuardInner>>,
}

#[pymethods]
impl PyBatchGuard {
    fn __enter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    #[pyo3(signature = (_exc_type=None, _exc_value=None, _traceback=None))]
    fn __exit__(
        &self,
        _exc_type: Option<Py<PyAny>>,
        _exc_value: Option<Py<PyAny>>,
        _traceback: Option<Py<PyAny>>,
    ) -> bool {
        if let Ok(mut g) = self.inner.write() {
            // Take + drop releases the inner guard (and hence the mutex).
            let _ = g.take();
        }
        false
    }

    fn __repr__(&self) -> &'static str {
        match self.inner.read() {
            Ok(g) if g.is_some() => "BatchGuard(active)",
            _ => "BatchGuard(released)",
        }
    }
}

#[pymethods]
impl PyIedServer {
    /// Construct a server hosting an IED defined in an SCL / ICD / CID file.
    ///
    /// `path` accepts ``str`` or any ``os.PathLike``. `ied_name` selects which
    /// ``<IED>`` in the document to instantiate.
    #[classmethod]
    #[pyo3(signature = (path, *, ied_name))]
    fn from_scl(
        cls: &Bound<'_, PyType>,
        py: Python<'_>,
        path: Bound<'_, PyAny>,
        ied_name: &str,
    ) -> PyResult<Self> {
        let path_str = extract_path_arg(path, "from_scl")?;
        let xml = std::fs::read_to_string(&path_str)
            .map_err(|e| pyo3::exceptions::PyOSError::new_err(format!("read {path_str}: {e}")))?;
        Self::from_scl_str(cls, py, &xml, ied_name)
    }

    /// Construct from an SCL XML string instead of a file path.
    #[classmethod]
    #[pyo3(signature = (xml, *, ied_name))]
    fn from_scl_str(
        _cls: &Bound<'_, PyType>,
        py: Python<'_>,
        xml: &str,
        ied_name: &str,
    ) -> PyResult<Self> {
        let raw = iec61850_scl::parse_scl(xml).map_err(|e| map_scl_error(py, e))?;
        let resolved = raw.resolve().map_err(|e| map_scl_error(py, e))?;
        let model = Arc::new(
            resolved
                .build_model(ied_name)
                .map_err(|e| map_scl_error(py, e))?,
        );
        Ok(Self::from_model_arc(model))
    }

    /// Construct a server from a Python dict describing the IED model, without
    /// any SCL round-trip. Use this when the IED schema is generated in
    /// Python — code-driven test rigs, dynamic device skeletons, or runtimes
    /// that prefer dict-driven configuration to XML.
    ///
    /// **Spec shape (top-level)**::
    ///
    ///     {
    ///       "ied_name": "IED1",
    ///       "lds": [
    ///         {
    ///           "inst": "LD0",                  # required
    ///           "ld_name": None,                # optional functional name
    ///           "lns": [
    ///             {
    ///               "lln0": True,               # convenience for LLN0
    ///               # or: "class": "GGIO", "prefix": "", "inst": "1"
    ///               "dos": [
    ///                 {
    ///                   "name": "Mod",
    ///                   "das": [
    ///                     {
    ///                       "name": "stVal", "fc": "ST", "type": "Enumerated",
    ///                       "trg_ops": ["data_changed"],
    ///                       "value": {"type": "int", "value": 1},
    ///                     },
    ///                   ],
    ///                   # Optional: "sub_dos": [...], "constructed_das": [...],
    ///                   #           "array_count": <int>
    ///                 },
    ///               ],
    ///               "datasets": [
    ///                 {"name": "Events", "entries": [
    ///                   {"ln_name": "GGIO1", "fc": "ST", "do_path": ["Ind1","stVal"]},
    ///                 ]},
    ///               ],
    ///               "rcbs": [
    ///                 {"name": "Events01", "buffered": False,
    ///                  "dataset_ref": "Events", "conf_rev": 1,
    ///                  "trg_ops": ["data_changed","integrity"],
    ///                  "opt_flds": ["seq_num","time_stamp"],
    ///                  "buf_tm_ms": 100, "intg_pd_ms": 0},
    ///               ],
    ///               "lcbs": [...],
    ///               "sgcb": {"num_of_sg": 3, "act_sg": 1},
    ///             },
    ///           ],
    ///         },
    ///       ],
    ///     }
    ///
    /// **DA `type`** is the spelling from IEC 61850-7-3 (``"Boolean"``,
    /// ``"Int32"``, ``"Float32"``, ``"Enumerated"``, ``"Timestamp"``,
    /// ``"Quality"``, …). Sized variants accept an object form
    /// ``{"type": "OctetString", "max_len": 64}``.
    ///
    /// **DA `value`** is a tagged dict: ``{"type": "int", "value": 1}``,
    /// ``{"type": "bool", "value": True}``, ``{"type": "float32", "value": 0.0}``,
    /// ``{"type": "visible_string", "value": "abc"}``,
    /// ``{"type": "octet_string", "value": b"..."}``,
    /// ``{"type": "bit_string", "padding": 3, "data": b"..."}``,
    /// ``{"type": "default"}``. Omit `value` to default to the type's zero.
    ///
    /// RCB / LCB / SGCB declared here land in the model and are picked up by
    /// `start()` exactly as their SCL-driven counterparts would be. Every
    /// register_* method works against the same `"LD/LN.DO[.DA]*"` paths as
    /// for an SCL-built model.
    #[classmethod]
    #[pyo3(signature = (spec))]
    fn from_model_spec(
        _cls: &Bound<'_, PyType>,
        py: Python<'_>,
        spec: &Bound<'_, PyDict>,
    ) -> PyResult<Self> {
        let model = Arc::new(build_model_from_spec(py, spec)?);
        Ok(Self::from_model_arc(model))
    }

    /// Configure the TCP bind address (``"host:port"``). Use port ``0`` for
    /// an OS-assigned port; read the chosen port back from `bound_addr`
    /// after `start()`.
    fn bind(&self, addr: &str) -> PyResult<()> {
        let parsed: SocketAddr = addr.parse().map_err(|_| {
            PyValueError::new_err(format!("bind: '{addr}' is not a valid socket address"))
        })?;
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted { bind_addr, .. } => {
                *bind_addr = Some(parsed);
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "bind: cannot reconfigure after start()",
            )),
        }
    }

    /// Enable server-side TLS (IEC 62351-3 profile).
    ///
    /// `server_cert_pem` is the leaf-first PEM chain; `server_key_pem` is the
    /// matching PKCS#8 private key. Both are required.
    ///
    /// Client authentication is opt-in. By default the server requests but
    /// does not require a client certificate. Set `allow_only_known_peers=True`
    /// together with one or more `known_peer_pems` to pin acceptable client
    /// certificates; supply `client_ca_pem` to validate the client chain
    /// against your own CA.
    ///
    /// `min_tls_version` / `max_tls_version` accept ``"tls1.2"`` or
    /// ``"tls1.3"`` (defaults: tls1.2-tls1.3). The cipher suite list is
    /// hard-coded to the IEC 62351-3 ECDHE-RSA-AES-GCM whitelist.
    ///
    /// Only valid before `start()`.
    #[pyo3(signature = (
        server_cert_pem, server_key_pem, *,
        client_ca_pem=None,
        allow_only_known_peers=false,
        known_peer_pems=Vec::new(),
        chain_validation=true,
        time_validation=true,
        crl_pems=Vec::new(),
        session_resumption=true,
        min_tls_version="tls1.2".to_string(),
        max_tls_version="tls1.3".to_string(),
    ))]
    #[allow(clippy::too_many_arguments)]
    fn with_tls(
        &self,
        server_cert_pem: Vec<u8>,
        server_key_pem: Vec<u8>,
        client_ca_pem: Option<Vec<u8>>,
        allow_only_known_peers: bool,
        known_peer_pems: Vec<Vec<u8>>,
        chain_validation: bool,
        time_validation: bool,
        crl_pems: Vec<Vec<u8>>,
        session_resumption: bool,
        min_tls_version: String,
        max_tls_version: String,
    ) -> PyResult<()> {
        if server_cert_pem.is_empty() {
            return Err(PyValueError::new_err(
                "with_tls: server_cert_pem must not be empty",
            ));
        }
        if server_key_pem.is_empty() {
            return Err(PyValueError::new_err(
                "with_tls: server_key_pem must not be empty",
            ));
        }
        let min_version = parse_tls_version(&min_tls_version)?;
        let max_version = parse_tls_version(&max_tls_version)?;
        let pt = PendingTls {
            server_cert_pem,
            server_key_pem,
            client_ca_pem,
            allow_only_known_peers,
            known_peer_pems,
            chain_validation,
            time_validation,
            crl_pems,
            session_resumption,
            min_version,
            max_version,
        };
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted { pending_tls, .. } => {
                if pending_tls.is_some() {
                    return Err(PyRuntimeError::new_err(
                        "with_tls: TLS has already been configured on this server",
                    ));
                }
                *pending_tls = Some(pt);
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "with_tls: must be called before start()",
            )),
        }
    }

    #[setter]
    fn set_max_connections(&self, n: usize) -> PyResult<()> {
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted { config, .. } => {
                config.max_mms_connections = n;
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "max_connections: cannot reconfigure after start()",
            )),
        }
    }

    #[setter]
    fn set_vendor(&self, value: Option<String>) -> PyResult<()> {
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted { config, .. } => {
                config.vendor_name = value;
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "vendor: cannot reconfigure after start()",
            )),
        }
    }

    #[setter]
    fn set_model_name(&self, value: Option<String>) -> PyResult<()> {
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted { config, .. } => {
                config.model_name = value;
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "model_name: cannot reconfigure after start()",
            )),
        }
    }

    #[setter]
    fn set_revision(&self, value: Option<String>) -> PyResult<()> {
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted { config, .. } => {
                config.revision = value;
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "revision: cannot reconfigure after start()",
            )),
        }
    }

    /// Whether the server is currently bound and accepting connections.
    #[getter]
    fn is_running(&self) -> bool {
        matches!(*self.state.lock().unwrap(), ServerState::Running { .. })
    }

    /// Actual bound socket address as ``"host:port"``. When bound to port 0
    /// this reflects the OS-assigned port. Raises `RuntimeError` if the
    /// server has not been started.
    #[getter]
    fn bound_addr(&self) -> PyResult<String> {
        match &*self.state.lock().unwrap() {
            ServerState::Running {
                handle: Some(h), ..
            } => Ok(h.bound_addr.to_string()),
            _ => Err(PyRuntimeError::new_err("bound_addr: server is not running")),
        }
    }

    /// Number of MMS clients currently connected.
    #[getter]
    fn connection_count(&self) -> PyResult<usize> {
        match &*self.state.lock().unwrap() {
            ServerState::Running { server, .. } => Ok(server.connection_count()),
            _ => Err(PyRuntimeError::new_err(
                "connection_count: server is not running",
            )),
        }
    }

    /// Bind the listener and start the accept loop. Coroutine resolves once
    /// the server is ready to accept connections.
    fn start<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let state_arc = Arc::clone(&self.state);
        let model = Arc::clone(&self.model);
        let locals = TaskLocals::with_running_loop(py)?.copy_context(py)?;
        future_into_py(py, async move {
            server_do_start(&state_arc, &model, locals).await
        })
    }

    /// Graceful shutdown. Coroutine resolves once the accept loop has stopped
    /// and per-connection tasks have finished.
    fn stop<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let state_arc = Arc::clone(&self.state);
        future_into_py(py, async move { server_do_stop(&state_arc).await })
    }

    fn __aenter__<'py>(slf: PyRef<'_, Self>, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let state_arc = Arc::clone(&slf.state);
        let model = Arc::clone(&slf.model);
        let locals = TaskLocals::with_running_loop(py)?.copy_context(py)?;
        let slf_obj: Py<Self> = slf.into();
        future_into_py(py, async move {
            server_do_start(&state_arc, &model, locals).await?;
            Ok(slf_obj)
        })
    }

    fn __aexit__<'py>(
        &self,
        py: Python<'py>,
        _exc_type: Py<PyAny>,
        _exc_value: Py<PyAny>,
        _traceback: Py<PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let state_arc = Arc::clone(&self.state);
        future_into_py(py, async move { server_do_stop(&state_arc).await })
    }

    /// Register a read callback for a single data attribute.
    ///
    /// The callback receives the address string and returns:
    ///
    /// - any supported scalar value (``bool`` / ``int`` / ``float`` /
    ///   ``str`` / ``bytes`` / ``list``) — used as the read result, the
    ///   server skips its cached value;
    /// - ``None`` — fall through to the server's cached value;
    /// - raises an exception — read fails. Set ``code`` on the exception
    ///   (e.g. ``"HardwareFault"``) to control the reported
    ///   `DataAccessError` variant; otherwise ``ObjectAccessDenied`` is
    ///   used.
    ///
    /// Registration is allowed before `start()` (queued) and during
    /// `Running` (installed immediately). Re-registering the same path
    /// replaces the previous callback.
    #[pyo3(signature = (path, callback))]
    fn on_read(&self, py: Python<'_>, path: &str, callback: Py<PyAny>) -> PyResult<()> {
        if !callback.bind(py).is_callable() {
            return Err(PyValueError::new_err("on_read: callback must be callable"));
        }
        let canonical = self.canonical_handler_path("on_read", path)?;
        let user_path = path.to_string();
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted {
                pending_handlers, ..
            } => {
                pending_handlers.push(PendingHandler::Read {
                    canonical,
                    user_path,
                    callback,
                });
                Ok(())
            }
            ServerState::Running { server, .. } => {
                let h: Arc<dyn RustReadHandler> = Arc::new(PyReadHandler {
                    user_path,
                    callback,
                });
                server
                    .install_read_handler(&canonical, h)
                    .map_err(map_server_error)
            }
            ServerState::Stopped => {
                Err(PyRuntimeError::new_err("on_read: server has been stopped"))
            }
        }
    }

    /// Register check / operate / wait callbacks for a single control object.
    ///
    /// `path` selects the control object (``"<LD>/<LN>.<DO>"`` — DO level,
    /// not DA). `ctl_model` chooses one of the five IEC 61850 control
    /// models: ``"status-only"``, ``"direct-normal"``, ``"sbo-normal"``,
    /// ``"direct-enhanced"``, ``"sbo-enhanced"``.
    ///
    /// The callbacks receive ``(path, ctl_val, action_dict)``:
    ///
    /// - ``check`` (sync): return value is ignored, raise to reject;
    /// - ``operate`` (sync **or** ``async``): return value is ignored on
    ///   success, raise to reject;
    /// - ``wait`` (sync **or** ``async``): same shape as ``operate``,
    ///   driven only by ``sbo-enhanced`` after the operate phase.
    ///
    /// Set ``add_cause`` on the raised exception (e.g.
    /// ``"BlockedByInterlocking"``, ``"NotSupported"``, or the numeric
    /// MMS code) to control the reported `ControlAddCause`; absence
    /// falls back to ``"Unknown"``.
    ///
    /// `action_dict` keys: ``ctl_num``, ``test``, ``synchro_check``,
    /// ``interlock_check``, ``is_select``, ``ctl_time_ms``, ``origin``
    /// (a sub-dict with ``or_cat`` and ``or_ident``).
    #[pyo3(signature = (
        path, *, ctl_model, check=None, operate=None, wait=None,
        sbo_timeout_ms=30_000, sbo_class="operate-once",
    ))]
    #[allow(clippy::too_many_arguments)]
    fn on_control(
        &self,
        py: Python<'_>,
        path: &str,
        ctl_model: &str,
        check: Option<Py<PyAny>>,
        operate: Option<Py<PyAny>>,
        wait: Option<Py<PyAny>>,
        sbo_timeout_ms: u32,
        sbo_class: &str,
    ) -> PyResult<()> {
        for (name, cb) in [("check", &check), ("operate", &operate), ("wait", &wait)] {
            if let Some(c) = cb {
                if !c.bind(py).is_callable() {
                    return Err(PyValueError::new_err(format!(
                        "on_control: {name} must be callable"
                    )));
                }
            }
        }
        if check.is_none() && operate.is_none() && wait.is_none() {
            return Err(PyValueError::new_err(
                "on_control: at least one of check / operate / wait is required",
            ));
        }
        let model = control_model_from_str(ctl_model)?;
        let sbo = sbo_class_from_str(sbo_class)?;
        let (domain, ln_name, do_name) = self.resolve_control_path("on_control", path)?;
        let config = RustControlObjectConfig {
            name: do_name,
            ln_name,
            domain,
            ctl_model: model,
            sbo_timeout_ms,
            sbo_class: sbo,
        };
        let pending = PendingControl {
            user_path: path.to_string(),
            config,
            check,
            operate,
            wait,
        };
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted {
                pending_handlers, ..
            } => {
                pending_handlers.push(PendingHandler::Control(pending));
                Ok(())
            }
            ServerState::Running {
                server,
                task_locals,
                ..
            } => {
                let entry = build_control_entry(pending, task_locals.clone());
                server.control_objects().register(entry);
                Ok(())
            }
            ServerState::Stopped => Err(PyRuntimeError::new_err(
                "on_control: server has been stopped",
            )),
        }
    }

    /// Register a write callback for a single data attribute.
    ///
    /// The callback receives the address string and the incoming value
    /// (decoded with the same type rules as ``update_*``) and returns:
    ///
    /// - ``True`` — accept; the server writes the value into its cache;
    /// - ``False`` or ``None`` — accept but the server does **not** update
    ///   its cache (the callback is responsible for the side effect);
    /// - raises an exception — reject. Set ``code`` on the exception
    ///   (e.g. ``"ObjectValueInvalid"``) to control the reported
    ///   `DataAccessError` variant; otherwise ``ObjectAccessDenied`` is
    ///   used.
    ///
    /// Registration is allowed before `start()` (queued) and during
    /// `Running` (installed immediately). Re-registering the same path
    /// replaces the previous callback.
    #[pyo3(signature = (path, callback))]
    fn on_write(&self, py: Python<'_>, path: &str, callback: Py<PyAny>) -> PyResult<()> {
        if !callback.bind(py).is_callable() {
            return Err(PyValueError::new_err("on_write: callback must be callable"));
        }
        let canonical = self.canonical_handler_path("on_write", path)?;
        let user_path = path.to_string();
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted {
                pending_handlers, ..
            } => {
                pending_handlers.push(PendingHandler::Write {
                    canonical,
                    user_path,
                    callback,
                });
                Ok(())
            }
            ServerState::Running { server, .. } => {
                let h: Arc<dyn RustWriteHandler> = Arc::new(PyWriteHandler {
                    user_path,
                    callback,
                });
                server
                    .install_write_access_handler(&canonical, h)
                    .map_err(map_server_error)
            }
            ServerState::Stopped => {
                Err(PyRuntimeError::new_err("on_write: server has been stopped"))
            }
        }
    }

    /// Register a server-side dataset (collection of data attributes).
    ///
    /// `name` follows the IEC 61850 convention ``"<LN>$<dsName>"``
    /// (e.g. ``"GGIO1$ds1"``). All entries must belong to the same logical
    /// device. Reference each entry by its user-facing path
    /// ``"<LD>/<LN>.<DO>.<DA>[.<sub>]*"``.
    ///
    /// Datasets are scoped to a single MMS domain. Once a dataset has been
    /// registered the same name cannot be redefined; doing so raises
    /// `ValueError`.
    ///
    /// Only valid before `start()`.
    #[pyo3(signature = (name, paths))]
    fn add_dataset(&self, name: &str, paths: Vec<String>) -> PyResult<()> {
        if name.is_empty() {
            return Err(PyValueError::new_err("add_dataset: name must not be empty"));
        }
        if paths.is_empty() {
            return Err(PyValueError::new_err(
                "add_dataset: dataset must have at least one entry",
            ));
        }
        let mut entries: Vec<RustDatasetEntry> = Vec::with_capacity(paths.len());
        let mut domain: Option<String> = None;
        for path in &paths {
            let (entry, dom) = self.build_dataset_entry("add_dataset", path)?;
            if let Some(existing) = &domain {
                if existing != &dom {
                    return Err(PyValueError::new_err(format!(
                        "add_dataset: entries must share a single LD; \
                         '{path}' is in '{dom}' but earlier entry is in '{existing}'"
                    )));
                }
            } else {
                domain = Some(dom);
            }
            entries.push(entry);
        }
        let domain = domain.expect("non-empty paths guarantees domain is Some");
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted {
                pending_handlers, ..
            } => {
                let duplicate = pending_handlers
                    .iter()
                    .any(|h| matches!(h, PendingHandler::Dataset(d) if d.name == name));
                if duplicate {
                    return Err(PyValueError::new_err(format!(
                        "add_dataset: dataset '{name}' is already registered"
                    )));
                }
                pending_handlers.push(PendingHandler::Dataset(PendingDataset {
                    name: name.to_string(),
                    domain,
                    entries,
                }));
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "add_dataset: must be called before start()",
            )),
        }
    }

    /// Register an Unbuffered Report Control Block (URCB).
    ///
    /// `path` selects the RCB at ``"<LD>/<LN>.<rcb_name>"``. `dataset` is the
    /// name of a previously registered dataset (see `add_dataset`).
    ///
    /// `trg_ops` is a list of trigger flags
    /// (``"data_changed"`` / ``"quality_changed"`` / ``"data_update"`` /
    /// ``"integrity"`` / ``"gi"`` / ``"all"`` / ``"none"``).
    ///
    /// `opt_flds` is a list of optional report fields
    /// (``"seq_num"`` / ``"time_stamp"`` / ``"reason"`` / ``"data_set"`` /
    /// ``"data_reference"`` / ``"conf_rev"`` / ``"buffer_overflow"`` /
    /// ``"entry_id"``). Note that ``buffer_overflow`` and ``entry_id`` are
    /// masked out on the wire for URCBs by IEC 61850-7-2 §15.
    ///
    /// Only valid before `start()`.
    #[pyo3(signature = (
        path, *, dataset, rpt_id=None, conf_rev=1,
        trg_ops=None, opt_flds=None, buf_tm_ms=0, intg_pd_ms=0,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn register_urcb(
        &self,
        path: &str,
        dataset: &str,
        rpt_id: Option<String>,
        conf_rev: u32,
        trg_ops: Option<Vec<String>>,
        opt_flds: Option<Vec<String>>,
        buf_tm_ms: u32,
        intg_pd_ms: u32,
    ) -> PyResult<()> {
        let (_, _ln_name, rcb_name, mms_path) = self.resolve_urcb_path("register_urcb", path)?;
        let trg_ops = match trg_ops {
            Some(v) => parse_trg_ops(&v)?,
            None => RustServerTriggerOptions::DATA_CHANGED,
        };
        let opt_flds = match opt_flds {
            Some(v) => parse_opt_flds(&v)?,
            None => {
                RustServerOptFlds::SEQ_NUM
                    | RustServerOptFlds::TIME_STAMP
                    | RustServerOptFlds::REASON
            }
        };
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted {
                pending_handlers, ..
            } => {
                let dataset_found = pending_handlers
                    .iter()
                    .any(|h| matches!(h, PendingHandler::Dataset(d) if d.name == dataset));
                if !dataset_found {
                    return Err(PyKeyError::new_err(format!(
                        "register_urcb: dataset '{dataset}' is not registered \
                         (call add_dataset first)"
                    )));
                }
                let duplicate = pending_handlers
                    .iter()
                    .any(|h| matches!(h, PendingHandler::Urcb(u) if u.mms_path == mms_path));
                if duplicate {
                    return Err(PyValueError::new_err(format!(
                        "register_urcb: URCB '{path}' is already registered"
                    )));
                }
                pending_handlers.push(PendingHandler::Urcb(PendingUrcb {
                    mms_path,
                    dataset_name: dataset.to_string(),
                    rcb_name,
                    rpt_id: rpt_id.unwrap_or_default(),
                    conf_rev,
                    trg_ops,
                    opt_flds,
                    buf_tm_ms,
                    intg_pd_ms,
                }));
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "register_urcb: must be called before start()",
            )),
        }
    }

    /// Register a Buffered Report Control Block (BRCB).
    ///
    /// Mirrors `register_urcb` but binds to a buffered MMS path
    /// (`"<LD>/<LN>.<rcb_name>"` resolves to ``"<domain>/<LN>$BR$<rcb_name>"``).
    ///
    /// `buffer_capacity` is the entry-count ring size (IEC 61850-7-2 §15
    /// IC-2 — entry-count, not byte-count); the default of 64 matches the
    /// underlying `iec61850-server` Brcb default. ``buffer_overflow`` and
    /// ``entry_id`` opt_flds are honored on the wire (URCB masks them out).
    ///
    /// `with_resv_tms` / `with_owner` toggle the Edition 2+ MMS fields.
    ///
    /// Only valid before `start()`.
    #[pyo3(signature = (
        path, *, dataset, rpt_id=None, conf_rev=1,
        trg_ops=None, opt_flds=None, buf_tm_ms=0, intg_pd_ms=0,
        buffer_capacity=64, with_resv_tms=true, with_owner=false,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn register_brcb(
        &self,
        path: &str,
        dataset: &str,
        rpt_id: Option<String>,
        conf_rev: u32,
        trg_ops: Option<Vec<String>>,
        opt_flds: Option<Vec<String>>,
        buf_tm_ms: u32,
        intg_pd_ms: u32,
        buffer_capacity: usize,
        with_resv_tms: bool,
        with_owner: bool,
    ) -> PyResult<()> {
        if buffer_capacity == 0 {
            return Err(PyValueError::new_err(
                "register_brcb: buffer_capacity must be >= 1",
            ));
        }
        let (_, _ln_name, rcb_name, mms_path) = self.resolve_brcb_path("register_brcb", path)?;
        let trg_ops = match trg_ops {
            Some(v) => parse_trg_ops(&v)?,
            None => RustServerTriggerOptions::DATA_CHANGED,
        };
        let opt_flds = match opt_flds {
            Some(v) => parse_opt_flds(&v)?,
            None => {
                RustServerOptFlds::SEQ_NUM
                    | RustServerOptFlds::TIME_STAMP
                    | RustServerOptFlds::REASON
                    | RustServerOptFlds::BUFFER_OVERFLOW
                    | RustServerOptFlds::ENTRY_ID
            }
        };
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted {
                pending_handlers, ..
            } => {
                let dataset_found = pending_handlers
                    .iter()
                    .any(|h| matches!(h, PendingHandler::Dataset(d) if d.name == dataset));
                if !dataset_found {
                    return Err(PyKeyError::new_err(format!(
                        "register_brcb: dataset '{dataset}' is not registered \
                         (call add_dataset first)"
                    )));
                }
                let duplicate = pending_handlers
                    .iter()
                    .any(|h| matches!(h, PendingHandler::Brcb(b) if b.mms_path == mms_path));
                if duplicate {
                    return Err(PyValueError::new_err(format!(
                        "register_brcb: BRCB '{path}' is already registered"
                    )));
                }
                pending_handlers.push(PendingHandler::Brcb(PendingBrcb {
                    mms_path,
                    dataset_name: dataset.to_string(),
                    rcb_name,
                    rpt_id: rpt_id.unwrap_or_default(),
                    conf_rev,
                    trg_ops,
                    opt_flds,
                    buf_tm_ms,
                    intg_pd_ms,
                    buffer_capacity,
                    with_resv_tms,
                    with_owner,
                }));
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "register_brcb: must be called before start()",
            )),
        }
    }

    /// Register a Log Control Block (LCB).
    ///
    /// `path` selects the block at ``"<LD>/<LN>.<lcb_name>"`` and resolves to
    /// the MMS-wire path ``"<domain>/<LN>$LG$<lcb_name>"``. `dataset` is the
    /// dataset reference recorded on the block (caller convention
    /// ``"<LN>$<dsName>"``); the dataset itself does not need to be registered
    /// with `add_dataset`, because journal entries are written by explicit
    /// `log_value` calls rather than auto-triggered on `update_*`.
    ///
    /// `trg_ops` advertises which trigger options the LCB declares on the wire
    /// (``"data_changed"`` / ``"quality_changed"`` / ``"data_update"`` /
    /// ``"integrity"`` / ``"gi"`` / ``"all"`` / ``"none"``); defaults to
    /// ``"data_changed"``.
    ///
    /// `storage_capacity` bounds the in-memory ring buffer (oldest entries are
    /// evicted on overflow); ``None`` keeps the journal unbounded. The
    /// in-memory backend is intended for tests and embedded scenarios.
    ///
    /// `default_enabled=True` flips `LogEna` to enabled at `start()`. Toggle
    /// at runtime via `set_log_ena`.
    ///
    /// Only valid before `start()`.
    #[pyo3(signature = (
        path, *, dataset, log_ref=None, trg_ops=None, intg_pd_ms=0,
        storage_capacity=None, include_reason_code=true, default_enabled=true,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn register_log_control(
        &self,
        path: &str,
        dataset: &str,
        log_ref: Option<String>,
        trg_ops: Option<Vec<String>>,
        intg_pd_ms: u32,
        storage_capacity: Option<usize>,
        include_reason_code: bool,
        default_enabled: bool,
    ) -> PyResult<()> {
        let (domain, _ln_name, lcb_name, mms_path) =
            self.resolve_lcb_path("register_log_control", path)?;
        let trg_ops = match trg_ops {
            Some(v) => parse_trg_ops(&v)?,
            None => RustServerTriggerOptions::DATA_CHANGED,
        };
        if let Some(cap) = storage_capacity {
            if cap == 0 {
                return Err(PyValueError::new_err(
                    "register_log_control: storage_capacity must be >= 1 or None for unbounded",
                ));
            }
        }
        // `(domain, item)` is the dispatch key the MMS layer reads from the
        // wire ReadJournal request. `mms_path` is `"<domain>/<item>"`, so
        // splitting on the first `/` recovers the item segment exactly.
        let item = mms_path
            .split_once('/')
            .map(|(_, i)| i.to_string())
            .unwrap_or_else(|| mms_path.clone());
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted {
                pending_handlers, ..
            } => {
                let duplicate = pending_handlers
                    .iter()
                    .any(|h| matches!(h, PendingHandler::LogControl(p) if p.mms_path == mms_path));
                if duplicate {
                    return Err(PyValueError::new_err(format!(
                        "register_log_control: LCB '{path}' is already registered"
                    )));
                }
                pending_handlers.push(PendingHandler::LogControl(PendingLogControl {
                    mms_path,
                    domain,
                    item,
                    lcb_name,
                    dataset_name: dataset.to_string(),
                    log_ref,
                    trg_ops,
                    intg_period_ms: intg_pd_ms,
                    include_reason_code,
                    default_enabled,
                    storage_capacity,
                }));
                Ok(())
            }
            _ => Err(PyRuntimeError::new_err(
                "register_log_control: must be called before start()",
            )),
        }
    }

    /// Toggle a registered LCB's `LogEna` state.
    ///
    /// `path` is the same ``"<LD>/<LN>.<lcb_name>"`` form accepted by
    /// `register_log_control`. Requires the server to be running. Enabling a
    /// block whose dataset is `None` raises `ValueError`.
    fn set_log_ena(&self, path: &str, on: bool) -> PyResult<()> {
        let (_domain, _ln_name, _lcb_name, mms_path) =
            self.resolve_lcb_path("set_log_ena", path)?;
        let (domain, item) = mms_path.split_once('/').ok_or_else(|| {
            PyValueError::new_err(format!("set_log_ena: malformed mms_path '{mms_path}'"))
        })?;
        let state = self.state.lock().unwrap();
        let server = match &*state {
            ServerState::Running { server, .. } => Arc::clone(server),
            _ => {
                return Err(PyRuntimeError::new_err(
                    "set_log_ena: server is not running",
                ));
            }
        };
        drop(state);
        let registry = server.log_controls();
        let g = registry
            .read()
            .map_err(|_| PyRuntimeError::new_err("set_log_ena: log_controls registry poisoned"))?;
        let lc = g
            .get(&(domain.to_string(), item.to_string()))
            .ok_or_else(|| {
                PyKeyError::new_err(format!("set_log_ena: LCB '{path}' is not registered"))
            })?;
        lc.set_log_ena(on)
            .map_err(|e| PyValueError::new_err(format!("set_log_ena: {e}")))
    }

    /// Write one journal entry to a registered LCB.
    ///
    /// `path` selects the block (``"<LD>/<LN>.<lcb_name>"``); `data_ref` is
    /// the attribute reference recorded on the entry (e.g.
    /// ``"DemoIEDLD0/GGIO1$ST$Ind1$stVal"``); `value` is converted to
    /// the closest `MmsValue` (bool → Boolean, int → Integer, float → Float32,
    /// str → VisibleString, bytes → OctetString, list → Array).
    ///
    /// `time_ms` defaults to the current wall clock (ms epoch); `reason_code`
    /// is the per-entry trigger bit-string (bit 1 = data_changed = ``0x02``,
    /// bit 2 = quality_changed = ``0x04``, …).
    ///
    /// Returns the assigned 8-byte entry id as a Python `int`, or `None` if
    /// the LCB is disabled (`LogEna = NotEnabled`) and the trigger was
    /// silently skipped. Requires the server to be running.
    #[pyo3(signature = (path, *, data_ref, value, time_ms=None, reason_code=0x02))]
    fn log_value(
        &self,
        path: &str,
        data_ref: &str,
        value: Bound<'_, PyAny>,
        time_ms: Option<u64>,
        reason_code: u8,
    ) -> PyResult<Option<u64>> {
        let (_domain_check, _ln_name, _lcb_name, mms_path) =
            self.resolve_lcb_path("log_value", path)?;
        let (domain, item) = mms_path.split_once('/').ok_or_else(|| {
            PyValueError::new_err(format!("log_value: malformed mms_path '{mms_path}'"))
        })?;
        let mms_value = py_value_to_mms_value_generic(&value)?;
        let state = self.state.lock().unwrap();
        let server = match &*state {
            ServerState::Running { server, .. } => Arc::clone(server),
            _ => {
                return Err(PyRuntimeError::new_err("log_value: server is not running"));
            }
        };
        drop(state);
        let registry = server.log_controls();
        let g = registry
            .read()
            .map_err(|_| PyRuntimeError::new_err("log_value: log_controls registry poisoned"))?;
        let lc = g
            .get(&(domain.to_string(), item.to_string()))
            .ok_or_else(|| {
                PyKeyError::new_err(format!("log_value: LCB '{path}' is not registered"))
            })?;
        let ts = time_ms.unwrap_or_else(|| {
            use std::time::{SystemTime, UNIX_EPOCH};
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0)
        });
        lc.log_single_value(ts, data_ref, mms_value, reason_code)
            .map(|opt_id| opt_id.map(|id| id.0))
            .map_err(|e| PyRuntimeError::new_err(format!("log_value: {e}")))
    }

    /// Install application callbacks for a Setting Group Control Block (SGCB).
    ///
    /// `ld_inst` selects the LD whose LLN0 declares the SGCB (one SGCB per
    /// LD per IEC 61850). All three callbacks are independently optional:
    ///
    /// - ``on_act_sg(new_sg: int, conn_id: int) -> bool`` — invoked on client
    ///   ``SelectActiveSG``; return ``False`` to reject with
    ///   ``ObjectAccessDenied``. Default: allow.
    /// - ``on_edit_sg(new_sg: int, conn_id: int) -> bool`` — invoked on
    ///   ``EditSG``; ``new_sg == 0`` is the cancel form. Default: allow.
    /// - ``on_confirm(edit_sg: int, conn_id: int) -> None`` — fired after
    ///   ``ConfirmEditSG`` passes all server-side guards, immediately before
    ///   the edit session is cleared. The application should persist any
    ///   staged FC=SE values here. No veto.
    ///
    /// The callbacks are queued at NotStarted and bound to the registry at
    /// `start()`. Calling at runtime is also supported and replaces any
    /// previously-installed handler atomically. The SGCB itself must be
    /// declared in the SCL (`<SettingControl numOfSGs="N"/>` on LN0) — if
    /// the LD has no SGCB this call raises `KeyError`.
    #[pyo3(signature = (ld_inst, *, on_act_sg=None, on_edit_sg=None, on_confirm=None))]
    fn register_setting_group_handler(
        &self,
        ld_inst: &str,
        on_act_sg: Option<Py<PyAny>>,
        on_edit_sg: Option<Py<PyAny>>,
        on_confirm: Option<Py<PyAny>>,
    ) -> PyResult<()> {
        let domain = self.resolve_ld_domain("register_setting_group_handler", ld_inst)?;
        let mut state = self.state.lock().unwrap();
        match &mut *state {
            ServerState::NotStarted {
                pending_handlers, ..
            } => {
                // De-dup is allowed at queue time: if an entry already exists for
                // this domain, the later call replaces it (mirrors runtime semantics).
                pending_handlers.retain(
                    |h| !matches!(h, PendingHandler::SettingGroup(p) if p.domain == domain),
                );
                pending_handlers.push(PendingHandler::SettingGroup(PendingSettingGroupHandler {
                    domain,
                    user_path: ld_inst.to_string(),
                    on_act_sg,
                    on_edit_sg,
                    on_confirm,
                }));
                Ok(())
            }
            ServerState::Running { server, .. } => {
                let server = Arc::clone(server);
                drop(state);
                let handler: Arc<dyn RustSettingGroupHandler> = Arc::new(PySettingGroupHandler {
                    on_act_sg,
                    on_edit_sg,
                    on_confirm,
                });
                server
                    .register_setting_group_handler(&domain, handler)
                    .map_err(|_| {
                        PyKeyError::new_err(format!(
                            "register_setting_group_handler: LD '{ld_inst}' has no SGCB"
                        ))
                    })
            }
            _ => Err(PyRuntimeError::new_err(
                "register_setting_group_handler: server is stopped",
            )),
        }
    }

    /// Read the current SGCB snapshot for one LD.
    ///
    /// Returns a dict with `num_of_sg`, `act_sg`, `edit_sg`, `cnf_edit`,
    /// `last_act_tm_ms`, `resv_tms_s` (`None` when `has_resv_tms=False` in
    /// the model). Raises `KeyError` if the LD has no SGCB. Requires the
    /// server to be running.
    fn get_setting_group_info<'py>(
        &self,
        py: Python<'py>,
        ld_inst: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let domain = self.resolve_ld_domain("get_setting_group_info", ld_inst)?;
        let state = self.state.lock().unwrap();
        let server = match &*state {
            ServerState::Running { server, .. } => Arc::clone(server),
            _ => {
                return Err(PyRuntimeError::new_err(
                    "get_setting_group_info: server is not running",
                ));
            }
        };
        drop(state);
        let registry = server.setting_groups();
        let rt = registry.lookup(&domain).ok_or_else(|| {
            PyKeyError::new_err(format!(
                "get_setting_group_info: LD '{ld_inst}' has no SGCB"
            ))
        })?;
        let snap: RustSgcbSnapshot = rt.snapshot();
        let dict = PyDict::new(py);
        dict.set_item("num_of_sg", snap.num_of_sg)?;
        dict.set_item("act_sg", snap.act_sg)?;
        dict.set_item("edit_sg", snap.edit_sg)?;
        dict.set_item("cnf_edit", snap.cnf_edit)?;
        dict.set_item("last_act_tm_ms", snap.last_act_tm_ms)?;
        match snap.resv_tms_s {
            Some(v) => dict.set_item("resv_tms_s", v)?,
            None => dict.set_item("resv_tms_s", py.None())?,
        }
        Ok(dict)
    }

    /// Force the active setting group, bypassing the `on_act_sg` callback.
    ///
    /// Intended for application startup (restoring persisted state) where
    /// the wire-level `SelectActiveSG` veto path does not apply. `sg` must
    /// be in `[1, num_of_sg]`. Raises `KeyError` if the LD has no SGCB,
    /// `ValueError` if `sg` is out of range. Requires the server to be
    /// running.
    fn force_active_setting_group(&self, ld_inst: &str, sg: u8) -> PyResult<()> {
        let domain = self.resolve_ld_domain("force_active_setting_group", ld_inst)?;
        let state = self.state.lock().unwrap();
        let server = match &*state {
            ServerState::Running { server, .. } => Arc::clone(server),
            _ => {
                return Err(PyRuntimeError::new_err(
                    "force_active_setting_group: server is not running",
                ));
            }
        };
        drop(state);
        server
            .force_active_setting_group(&domain, sg)
            .map_err(|err| {
                let msg = err.to_string();
                if msg.contains("no setting group control block") {
                    PyKeyError::new_err(format!(
                        "force_active_setting_group: LD '{ld_inst}' has no SGCB"
                    ))
                } else {
                    PyValueError::new_err(format!("force_active_setting_group: {msg}"))
                }
            })
    }

    /// Acquire the server's data-model lock for an atomic batch update.
    ///
    /// While the returned guard is held, no other caller can take the lock
    /// (re-entry raises `RuntimeError`). Typical use:
    ///
    /// .. code:: python
    ///
    ///     with server.batch():
    ///         server.update_bool("LD/LN.Ind1.stVal", True)
    ///         server.update_float32("LD/LN.AnIn1.mag.f", 12.5)
    ///
    /// Raises `RuntimeError` if the server is not running or another batch
    /// is already in progress.
    fn batch(&self) -> PyResult<PyBatchGuard> {
        let state = self.state.lock().unwrap();
        let server = match &*state {
            ServerState::Running { server, .. } => Arc::clone(server),
            _ => {
                return Err(PyRuntimeError::new_err("batch: server is not running"));
            }
        };
        drop(state);
        let guard = server.lock_data_model().map_err(map_server_error)?;
        // SAFETY: `guard` borrows from `*server`. The lifetime is widened to
        // `'static` and the `Arc<IedServer>` is co-located in the same
        // `BatchGuardInner`. Field-drop order drops `_guard` before
        // `_server`, so the borrow stays valid for as long as the guard
        // exists.
        let guard_static: RustDataModelGuard<'static> = unsafe { std::mem::transmute(guard) };
        Ok(PyBatchGuard {
            inner: StdRwLock::new(Some(BatchGuardInner {
                _guard: guard_static,
                _server: server,
            })),
        })
    }

    /// Update a ``BOOLEAN`` data attribute.
    fn update_bool(&self, path: &str, value: bool) -> PyResult<()> {
        self.with_da("update_bool", path, |s, da| s.update_boolean(da, value))
    }

    /// Update an ``INT32`` data attribute.
    fn update_int32(&self, path: &str, value: i32) -> PyResult<()> {
        self.with_da("update_int32", path, |s, da| s.update_int32(da, value))
    }

    /// Update an ``INT64`` data attribute.
    fn update_int64(&self, path: &str, value: i64) -> PyResult<()> {
        self.with_da("update_int64", path, |s, da| s.update_int64(da, value))
    }

    /// Update an ``INT32U`` / ``INT8U`` / ``INT16U`` data attribute.
    fn update_uint32(&self, path: &str, value: u32) -> PyResult<()> {
        self.with_da("update_uint32", path, |s, da| s.update_unsigned(da, value))
    }

    /// Update a ``FLOAT32`` data attribute.
    fn update_float32(&self, path: &str, value: f32) -> PyResult<()> {
        self.with_da("update_float32", path, |s, da| s.update_float32(da, value))
    }

    /// Update a ``FLOAT64`` data attribute.
    fn update_float64(&self, path: &str, value: f64) -> PyResult<()> {
        self.with_da("update_float64", path, |s, da| s.update_float64(da, value))
    }

    /// Update a ``VisibleString`` data attribute.
    fn update_string(&self, path: &str, value: String) -> PyResult<()> {
        self.with_da("update_string", path, |s, da| {
            s.update_visible_string(da, value)
        })
    }

    fn __repr__(&self) -> String {
        let state = self.state.lock().unwrap();
        let tag = match &*state {
            ServerState::NotStarted { .. } => "not-started",
            ServerState::Running { .. } => "running",
            ServerState::Stopped => "stopped",
        };
        format!("IedServer(state={tag})")
    }
}

#[pymodule]
fn _native(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;

    m.add("IedError", py.get_type::<IedError>())?;
    m.add("IedConnectionError", py.get_type::<IedConnectionError>())?;
    m.add("IedTimeoutError", py.get_type::<IedTimeoutError>())?;
    m.add("IedDataAccessError", py.get_type::<IedDataAccessError>())?;
    m.add("IedServiceError", py.get_type::<IedServiceError>())?;
    m.add("IedControlError", py.get_type::<IedControlError>())?;
    m.add("SclError", py.get_type::<SclError>())?;
    m.add("IedServerError", py.get_type::<IedServerError>())?;

    m.add_class::<PyIedConnection>()?;
    m.add_class::<PyRcbHandle>()?;
    m.add_class::<PyControlObjectClient>()?;
    m.add_class::<PyScl>()?;
    m.add_class::<PyIedServer>()?;
    m.add_class::<PyBatchGuard>()?;
    m.add_function(wrap_pyfunction!(parse_scl, m)?)?;
    m.add_function(wrap_pyfunction!(load_scl, m)?)?;
    m.add_function(wrap_pyfunction!(query_sntp, m)?)?;

    Ok(())
}

"""The route is tested without a Temporal server: get_temporal_client is
replaced with a stub client whose handles return or raise whatever the test
needs."""

import pytest
from fastapi.testclient import TestClient
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

import routes.process
import utils.temporal_client
from main import app
from schemas.process_pdf_result import ProcessPdfResult

RESULT = ProcessPdfResult(
    task_id="abc123",
    pdf_bucket="test-pdfs",
    pdf_key="report-abc123.pdf",
    md_bucket="test-mds",
    md_key="report-abc123.md",
    local_pdf="/tmp/TEMP_PDF/report-abc123.pdf",
    local_md="/tmp/TEMP_MD/report-abc123.md",
    markdown_characters=80,
    workflow_id="process-pdf-abc123",
)


class StubDescription:
    def __init__(self, status):
        self.status = status


class StubHandle:
    """Stands in for temporalio.client.WorkflowHandle."""

    def __init__(self, workflow_id, result=RESULT, error=None, status=WorkflowExecutionStatus.COMPLETED):
        self.id = workflow_id
        self._result = result
        self._error = error
        self._status = status

    async def result(self):
        if self._error is not None:
            raise self._error
        return self._result

    async def describe(self):
        if isinstance(self._status, Exception):
            raise self._status
        return StubDescription(self._status)

    async def cancel(self):
        self._status = WorkflowExecutionStatus.CANCELED


class StubClient:
    def __init__(self):
        self.calls = []
        self.handle_kwargs = {}
        self.start_error = None

    async def start_workflow(self, run_fn, arg, *, id, task_queue, **kwargs):
        self.calls.append({"arg": arg, "id": id, "task_queue": task_queue})
        if self.start_error is not None:
            raise self.start_error
        return StubHandle(id, **self.handle_kwargs)

    def get_workflow_handle_for(self, run_fn, workflow_id, **kwargs):
        return StubHandle(workflow_id, **self.handle_kwargs)


@pytest.fixture
def temporal(monkeypatch):
    stub = StubClient()

    async def fake_get_client():
        return stub

    monkeypatch.setattr(routes.process, "get_temporal_client", fake_get_client)
    monkeypatch.setattr(utils.temporal_client, "_client", None)
    return stub


@pytest.fixture
def client(s3, temporal):
    """The s3 and temporal fixtures keep these requests off the network."""
    return TestClient(app)


def upload(client, pdf_bytes, **params):
    return client.post("/process", files={"file": ("report.pdf", pdf_bytes, "application/pdf")}, params=params)


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_same_idempotency_key_returns_the_original_process_task(client, pdf_bytes, temporal):
    first = client.post(
        "/process",
        files={"file": ("report.pdf", pdf_bytes, "application/pdf")},
        headers={"Idempotency-Key": "process-retry"},
    )
    second = client.post(
        "/process",
        files={"file": ("report.pdf", pdf_bytes, "application/pdf")},
        headers={"Idempotency-Key": "process-retry"},
    )

    assert first.status_code == second.status_code == 202
    assert second.json()["task_id"] == first.json()["task_id"]
    assert len(temporal.calls) == 1


def test_process_idempotency_key_rejects_a_different_file(client, pdf_bytes):
    client.post(
        "/process",
        files={"file": ("report.pdf", pdf_bytes, "application/pdf")},
        headers={"Idempotency-Key": "process-conflict"},
    )

    response = client.post(
        "/process",
        files={"file": ("report.pdf", b"%PDF-different", "application/pdf")},
        headers={"Idempotency-Key": "process-conflict"},
    )

    assert response.status_code == 409


# --- POST /process, non-blocking by default -------------------------------


def test_process_returns_202_immediately(client, pdf_bytes):
    response = upload(client, pdf_bytes)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing"
    assert body["task_id"]
    assert body["workflow_id"] == f"process-pdf-{body['task_id']}"


def test_the_202_body_names_where_the_pdf_went(client, pdf_bytes, settings):
    body = upload(client, pdf_bytes).json()

    assert body["pdf_bucket"] == settings.s3_pdf_bucket
    assert body["pdf_key"].startswith("report-")
    assert body["md_key"].endswith(".md")


def test_process_does_not_wait_for_the_result(client, pdf_bytes, temporal):
    """The handle's result must not be awaited on the default path."""
    temporal.handle_kwargs = {"error": AssertionError("result() should not be called")}

    assert upload(client, pdf_bytes).status_code == 202


def test_process_uploads_the_pdf_before_starting_the_workflow(client, s3, settings, pdf_bytes):
    """The document must reach the bucket, not the workflow history."""
    upload(client, pdf_bytes)

    uploaded = [key for (bucket, key) in s3.objects if bucket == settings.s3_pdf_bucket]
    assert len(uploaded) == 1
    assert uploaded[0].startswith("report-")


def test_process_starts_the_workflow_with_only_the_keys(client, pdf_bytes, temporal):
    upload(client, pdf_bytes)

    assert len(temporal.calls) == 1
    payload = temporal.calls[0]["arg"]
    assert payload.pdf_key.startswith("report-")
    assert payload.md_key.endswith(".md")


def test_process_uses_the_configured_task_queue(client, pdf_bytes, settings, temporal):
    upload(client, pdf_bytes)

    assert temporal.calls[0]["task_queue"] == settings.temporal_task_queue


def test_the_workflow_id_is_the_task_id(client, pdf_bytes, temporal):
    upload(client, pdf_bytes)

    call = temporal.calls[0]
    assert call["id"] == f"process-pdf-{call['arg'].task_id}"


def test_each_upload_gets_its_own_task_id(client, pdf_bytes, temporal):
    """Two uploads of the same file must not collide."""
    upload(client, pdf_bytes)
    upload(client, pdf_bytes)

    first, second = (call["arg"].task_id for call in temporal.calls)
    assert first != second
    assert temporal.calls[0]["id"] != temporal.calls[1]["id"]


def test_the_task_id_ties_the_keys_together(client, pdf_bytes, temporal):
    upload(client, pdf_bytes)

    payload = temporal.calls[0]["arg"]
    assert payload.task_id in payload.pdf_key
    assert payload.task_id in payload.md_key


# --- POST /process?wait=true, the blocking path ---------------------------


def test_wait_true_returns_the_full_result(client, pdf_bytes):
    response = upload(client, pdf_bytes, wait="true")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["markdown_characters"] == 80
    assert body["md_bucket"] == "test-mds"


def test_wait_false_is_the_default(client, pdf_bytes):
    assert upload(client, pdf_bytes, wait="false").status_code == 202


def test_a_workflow_failure_while_waiting_is_500(client, pdf_bytes, temporal):
    from exceptions.workflow import WorkflowExecutionError

    temporal.handle_kwargs = {"error": WorkflowExecutionError("activity gave up after 3 attempts")}

    response = upload(client, pdf_bytes, wait="true")

    assert response.status_code == 500
    assert "workflow failed" in response.json()["detail"]


# --- GET /process/{task_id} ----------------------------------------------


def test_status_while_running(client, temporal):
    temporal.handle_kwargs = {"status": WorkflowExecutionStatus.RUNNING}

    response = client.get("/process/abc123")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processing"
    assert body["task_id"] == "abc123"


def test_status_when_completed_returns_the_result(client, temporal):
    temporal.handle_kwargs = {"status": WorkflowExecutionStatus.COMPLETED}

    response = client.get("/process/abc123")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["md_key"] == "report-abc123.md"
    assert body["markdown_characters"] == 80


def test_status_for_a_terminated_task(client, temporal):
    temporal.handle_kwargs = {"status": WorkflowExecutionStatus.TERMINATED}

    body = client.get("/process/abc123").json()

    assert body["status"] == "terminated"


def test_status_for_a_failed_task(client, temporal):
    temporal.handle_kwargs = {"status": WorkflowExecutionStatus.FAILED}

    body = client.get("/process/abc123").json()

    assert body["status"] == "failed"


def test_status_for_an_unknown_task_is_404(client, temporal):
    temporal.handle_kwargs = {"status": RPCError("not found", RPCStatusCode.NOT_FOUND, "")}

    response = client.get("/process/nope")

    assert response.status_code == 404
    assert "no such task" in response.json()["detail"]


def test_the_status_endpoint_builds_the_same_workflow_id(client, temporal):
    temporal.handle_kwargs = {"status": WorkflowExecutionStatus.RUNNING}

    body = client.get("/process/abc123").json()

    assert body["workflow_id"] == routes.process.workflow_id_for("abc123")


# --- validation and failures ---------------------------------------------


def test_process_rejects_a_non_pdf(client):
    response = client.post("/process", files={"file": ("notes.txt", b"hello", "text/plain")})

    assert response.status_code == 400
    assert "only .pdf files are accepted" in response.json()["detail"]


def test_process_rejects_an_empty_file(client):
    response = client.post("/process", files={"file": ("empty.pdf", b"", "application/pdf")})

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_process_without_a_file_field_is_422(client):
    """This is the error you get from Postman when the form key is not 'file'."""
    response = client.post("/process")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "file"]


def test_process_with_the_wrong_field_name_is_422(client, pdf_bytes):
    response = client.post("/process", files={"intro_to_stats": ("report.pdf", pdf_bytes, "application/pdf")})

    assert response.status_code == 422


def test_a_storage_failure_is_502(client, s3, monkeypatch, pdf_bytes):
    """The upload happens in the route, so a bucket failure surfaces directly."""
    from botocore.exceptions import ClientError

    def explode(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "PutObject")

    monkeypatch.setattr(s3, "upload_file", explode)

    response = upload(client, pdf_bytes)

    assert response.status_code == 502
    assert "storage error" in response.json()["detail"]


def test_an_unreachable_temporal_is_503(client, monkeypatch, pdf_bytes):
    from exceptions.workflow import TemporalConnectionError

    async def explode():
        raise TemporalConnectionError("could not reach Temporal at localhost:7233")

    monkeypatch.setattr(routes.process, "get_temporal_client", explode)

    response = upload(client, pdf_bytes)

    assert response.status_code == 503
    assert "temporal unavailable" in response.json()["detail"]


def test_an_unreachable_temporal_is_503_on_status_too(client, monkeypatch):
    from exceptions.workflow import TemporalConnectionError

    async def explode():
        raise TemporalConnectionError("could not reach Temporal at localhost:7233")

    monkeypatch.setattr(routes.process, "get_temporal_client", explode)

    assert client.get("/process/abc123").status_code == 503


def test_an_unexpected_error_is_still_500(client, temporal, pdf_bytes):
    temporal.start_error = RuntimeError("something nobody planned for")

    assert upload(client, pdf_bytes).status_code == 500

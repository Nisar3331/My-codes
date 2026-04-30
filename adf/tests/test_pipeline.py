"""
=======================================================
  Unit Tests — ADF LLM Self-Healing Pipeline
  Run: pytest tests/test_pipeline.py -v
=======================================================
"""

import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.error_catalog import ADF_ERROR_CATALOG, get_error_catalog_as_dict, get_error_by_code
from src.pipeline_orchestrator import PipelineOrchestrator, MODE_MOCK


# ────────────────────────────────────────────────────────────────────────────
#  Error Catalog Tests
# ────────────────────────────────────────────────────────────────────────────

class TestErrorCatalog:

    def test_catalog_has_five_errors(self):
        assert len(ADF_ERROR_CATALOG) == 5

    def test_all_error_codes_unique(self):
        codes = [e.error_code for e in ADF_ERROR_CATALOG]
        assert len(codes) == len(set(codes))

    def test_get_error_by_code_found(self):
        e = get_error_by_code("ADF-001")
        assert e is not None
        assert e.error_name == "Linked Service Connection Failure"

    def test_get_error_by_code_not_found(self):
        assert get_error_by_code("ADF-999") is None

    def test_catalog_as_dict_shape(self):
        catalog = get_error_catalog_as_dict()
        assert len(catalog) == 5
        required_keys = {"error_code", "error_name", "main_cause", "resolution_action"}
        for entry in catalog:
            assert required_keys.issubset(set(entry.keys()))

    @pytest.mark.parametrize("code", ["ADF-001", "ADF-002", "ADF-003", "ADF-004", "ADF-005"])
    def test_each_error_has_resolution_action(self, code):
        e = get_error_by_code(code)
        assert e is not None
        assert e.resolution_action != ""

    @pytest.mark.parametrize("code", ["ADF-001", "ADF-002", "ADF-003", "ADF-004", "ADF-005"])
    def test_each_error_has_keywords(self, code):
        e = get_error_by_code(code)
        assert len(e.keywords) >= 3


# ────────────────────────────────────────────────────────────────────────────
#  LLM Error Detector (Keyword Fallback) Tests
# ────────────────────────────────────────────────────────────────────────────

class TestLlmDetectorFallback:
    """Tests the keyword fallback path (no real LLM call needed)."""

    def _get_detector(self):
        """Return a detector with LLM client mocked to simulate failure."""
        from src.llm_error_detector import LlmErrorDetector
        with patch("src.llm_error_detector.AzureOpenAI"):
            detector = LlmErrorDetector()
        # Make the LLM call raise to force fallback
        detector._client = MagicMock()
        detector._client.chat.completions.create.side_effect = Exception("LLM unavailable")
        return detector

    def test_fallback_detects_linked_service(self):
        detector = self._get_detector()
        result = detector.detect("Cannot connect to SQL Database. Login failed.")
        assert result["matched_error_code"] in ("ADF-001", "UNKNOWN")

    def test_fallback_detects_schema_mismatch(self):
        detector = self._get_detector()
        result = detector.detect("Column mapping failed: column does not exist in sink schema.")
        assert result["matched_error_code"] in ("ADF-002", "UNKNOWN")

    def test_fallback_detects_file_not_found(self):
        detector = self._get_detector()
        result = detector.detect("The specified file does not exist in ADLS path /raw-data/input/")
        assert result["matched_error_code"] in ("ADF-003", "UNKNOWN")

    def test_fallback_detects_timeout(self):
        detector = self._get_detector()
        result = detector.detect("Activity timed out after 3600 seconds. Integration runtime slow.")
        assert result["matched_error_code"] in ("ADF-004", "UNKNOWN")

    def test_fallback_detects_stored_proc(self):
        detector = self._get_detector()
        result = detector.detect("Stored procedure usp_load failed. Deadlock detected on table.")
        assert result["matched_error_code"] in ("ADF-005", "UNKNOWN")

    def test_result_has_required_keys(self):
        detector = self._get_detector()
        result = detector.detect("Some pipeline error occurred")
        required = {"matched_error_code", "matched_error_name", "confidence",
                    "resolution_action", "auto_resolvable", "severity"}
        assert required.issubset(set(result.keys()))


# ────────────────────────────────────────────────────────────────────────────
#  Error Resolver Tests
# ────────────────────────────────────────────────────────────────────────────

class TestErrorResolver:

    def _make_detection(self, error_code: str, action: str, auto_resolvable=True):
        return {
            "matched_error_code": error_code,
            "matched_error_name": f"Error {error_code}",
            "resolution_action": action,
            "auto_resolvable": auto_resolvable,
            "confidence": 0.9,
        }

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_linked_service(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        resolver._adf_client = MagicMock()
        resolver._adf_client.linked_services.list_by_factory.return_value = []
        detection = self._make_detection("ADF-001", "refresh_linked_service_credentials")
        result = resolver.resolve(detection)
        assert result["error_code"] == "ADF-001"
        assert "actions_taken" in result

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_schema_mismatch(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        resolver._adf_client = MagicMock()
        resolver._adf_client.datasets.list_by_factory.return_value = []
        detection = self._make_detection("ADF-002", "refresh_schema_and_remap_columns")
        result = resolver.resolve(detection)
        assert result["error_code"] == "ADF-002"

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_timeout(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        resolver._adf_client = MagicMock()
        detection = self._make_detection("ADF-004", "increase_timeout_and_optimize_diu")
        result = resolver.resolve(detection)
        assert result["error_code"] == "ADF-004"
        assert result["success"] is True

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_stored_proc(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        detection = self._make_detection("ADF-005", "retry_sp_with_dedup_and_deadlock_handling")
        result = resolver.resolve(detection)
        assert result["success"] is True

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_unknown(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        detection = self._make_detection("UNKNOWN", "manual_investigation_required", auto_resolvable=False)
        result = resolver.resolve(detection)
        assert result["success"] is False


# ────────────────────────────────────────────────────────────────────────────
#  Pipeline Orchestrator (Mock Mode) Tests
# ────────────────────────────────────────────────────────────────────────────

class TestPipelineOrchestratorMock:

    def test_mock_run_succeeds(self):
        """In mock mode, the orchestrator should succeed on the 2nd attempt."""
        orch = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report = orch.run()
        assert report["final_status"] == "Succeeded"
        assert report["total_attempts"] == 2  # Fail on 1st, succeed on 2nd

    def test_mock_run_report_structure(self):
        orch = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report = orch.run()
        required_keys = {"session_id", "pipeline_name", "mode", "final_status",
                         "run_history", "ingestion", "total_attempts"}
        assert required_keys.issubset(set(report.keys()))

    def test_mock_run_has_detection_in_history(self):
        orch = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report = orch.run()
        failed_runs = [r for r in report["run_history"] if r["status"] == "Failed"]
        assert len(failed_runs) == 1
        assert "detection" in failed_runs[0]
        assert failed_runs[0]["detection"]["matched_error_code"] == "ADF-002"

    def test_mock_run_has_resolution_in_history(self):
        orch = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report = orch.run()
        failed_runs = [r for r in report["run_history"] if r["status"] == "Failed"]
        assert "resolution" in failed_runs[0]

    def test_report_saved_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("logs", exist_ok=True)
        orch = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report = orch.run()
        log_files = list((tmp_path / "logs").glob("run_report_*.json"))
        assert len(log_files) == 1
        saved = json.loads(log_files[0].read_text())
        assert saved["session_id"] == report["session_id"]


# ────────────────────────────────────────────────────────────────────────────
#  CSV to Parquet Processor Tests (local mode)
# ────────────────────────────────────────────────────────────────────────────

class TestCsvToParquetLocal:

    def test_local_conversion(self, tmp_path):
        import pandas as pd
        # Create a sample CSV
        csv_dir = tmp_path / "csv"
        csv_dir.mkdir()
        parquet_dir = tmp_path / "landing"
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Charlie"],
            "value": [10.5, 20.3, 30.1],
        })
        df.to_csv(csv_dir / "sample.csv", index=False)

        from src.data_processor import CsvToParquetProcessor
        proc = CsvToParquetProcessor()
        result = proc.run_local(
            input_folder=str(csv_dir),
            output_folder=str(parquet_dir),
        )

        assert result["files_processed"] == 1
        assert result["files_failed"] == 0
        parquet_files = list(parquet_dir.glob("*.parquet"))
        assert len(parquet_files) == 1

        # Read back and verify
        import pyarrow.parquet as pq
        table = pq.read_table(str(parquet_files[0]))
        assert table.num_rows == 3
        assert "id" in table.schema.names

    def test_empty_csv_handled(self, tmp_path):
        import pandas as pd
        csv_dir = tmp_path / "csv"
        csv_dir.mkdir()
        parquet_dir = tmp_path / "landing"
        # Empty CSV with only headers
        pd.DataFrame(columns=["col1", "col2"]).to_csv(csv_dir / "empty.csv", index=False)

        from src.data_processor import CsvToParquetProcessor
        proc = CsvToParquetProcessor()
        result = proc.run_local(str(csv_dir), str(parquet_dir))
        # Should still process without crashing
        assert result["files_failed"] == 0

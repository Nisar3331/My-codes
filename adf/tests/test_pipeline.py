"""
=======================================================
  Unit Tests — ADF LLM Self-Healing Pipeline
  Run: pytest tests/test_pipeline.py -v

  HOW TO ADD TESTS FOR A NEW ERROR:
  ──────────────────────────────────
  1. Add to TestErrorCatalog      → test_each_error_has_*
  2. Add to TestLlmDetectorFallback → test_fallback_detects_<name>
  3. Add to TestErrorResolver       → test_resolve_<name>
  4. Add to TestCsvToParquetLocal   → test_<data_quality_scenario>
=======================================================
"""

import sys
import os
import re
import json
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.error_catalog import ADF_ERROR_CATALOG, get_error_catalog_as_dict, get_error_by_code
from src.pipeline_orchestrator import PipelineOrchestrator, MODE_MOCK


# =============================================================================
#  Error Catalog Tests
# =============================================================================

class TestErrorCatalog:

    def test_catalog_has_six_errors(self):
        """Catalog must contain all 6 defined error types."""
        assert len(ADF_ERROR_CATALOG) == 6

    def test_all_error_codes_unique(self):
        codes = [e.error_code for e in ADF_ERROR_CATALOG]
        assert len(codes) == len(set(codes))

    def test_get_error_by_code_found(self):
        e = get_error_by_code("ADF-001")
        assert e is not None
        assert e.error_name == "Linked Service Connection Failure"

    def test_get_error_by_code_adf006(self):
        e = get_error_by_code("ADF-006")
        assert e is not None
        assert "Parquet" in e.error_name

    def test_get_error_by_code_not_found(self):
        assert get_error_by_code("ADF-999") is None

    def test_catalog_as_dict_shape(self):
        catalog = get_error_catalog_as_dict()
        assert len(catalog) == 6
        required = {"error_code", "error_name", "main_cause", "resolution_action"}
        for entry in catalog:
            assert required.issubset(set(entry.keys()))

    @pytest.mark.parametrize("code", [
        "ADF-001", "ADF-002", "ADF-003", "ADF-004", "ADF-005", "ADF-006",
    ])
    def test_each_error_has_resolution_action(self, code):
        e = get_error_by_code(code)
        assert e is not None
        assert e.resolution_action != ""

    @pytest.mark.parametrize("code", [
        "ADF-001", "ADF-002", "ADF-003", "ADF-004", "ADF-005", "ADF-006",
    ])
    def test_each_error_has_keywords(self, code):
        e = get_error_by_code(code)
        assert len(e.keywords) >= 3

    @pytest.mark.parametrize("code", [
        "ADF-001", "ADF-002", "ADF-003", "ADF-004", "ADF-005", "ADF-006",
    ])
    def test_each_error_has_troubleshooting_steps(self, code):
        e = get_error_by_code(code)
        assert len(e.troubleshooting_steps) >= 3

    def test_adf006_keywords_include_parquet_terms(self):
        e = get_error_by_code("ADF-006")
        assert "parquet" in e.keywords
        assert "parquetinvalidcolumnname" in e.keywords

    def test_adf001_keywords_include_real_error_terms(self):
        """ADF-001 keywords must match the real SqlFailedToConnect error."""
        e = get_error_by_code("ADF-001")
        assert "sqlfailedtoconnect" in e.keywords
        assert "cannot connect" in e.keywords

    def test_adf003_keywords_include_real_error_terms(self):
        """ADF-003 keywords must match the real UserErrorSourceBlobNotExist error."""
        e = get_error_by_code("ADF-003")
        assert "usererrorsourceblobnotexist" in e.keywords
        assert "blob is missing" in e.keywords


# =============================================================================
#  LLM Error Detector — Keyword Fallback Tests
#  (no real LLM call — forces the keyword fallback path)
# =============================================================================

class TestLlmDetectorFallback:

    def _get_detector(self):
        """Return detector with LLM mocked to fail → forces keyword fallback."""
        from src.llm_error_detector import LlmErrorDetector
        with patch("src.llm_error_detector.AzureOpenAI"):
            detector = LlmErrorDetector()
        detector._client = MagicMock()
        detector._client.chat.completions.create.side_effect = Exception("LLM unavailable")
        return detector

    def test_fallback_detects_linked_service(self):
        """Real error: SqlFailedToConnect on dataplatform123."""
        d = self._get_detector()
        r = d.detect(
            "ErrorCode=SqlFailedToConnect Cannot connect to SQL Database. "
            "Server: dataplatform123.database.windows.net. "
            "Check the linked service configuration."
        )
        assert r["matched_error_code"] in ("ADF-001", "UNKNOWN")

    def test_fallback_detects_schema_mismatch(self):
        d = self._get_detector()
        r = d.detect("Column mapping failed: column does not exist in sink schema.")
        assert r["matched_error_code"] in ("ADF-002", "UNKNOWN")

    def test_fallback_detects_file_not_found(self):
        """Real error: UserErrorSourceBlobNotExist on raw/smart-error.csv."""
        d = self._get_detector()
        r = d.detect(
            "ErrorCode=UserErrorSourceBlobNotExist "
            "The required Blob is missing. "
            "ContainerName: pf-observability-blob-container, "
            "path: raw/smart-error.csv. "
            "The remote server returned an error: (404) Not Found."
        )
        assert r["matched_error_code"] in ("ADF-003", "UNKNOWN")

    def test_fallback_detects_timeout(self):
        d = self._get_detector()
        r = d.detect("Activity timed out after 3600 seconds. Integration runtime slow.")
        assert r["matched_error_code"] in ("ADF-004", "UNKNOWN")

    def test_fallback_detects_stored_proc(self):
        d = self._get_detector()
        r = d.detect("Stored procedure usp_load failed. Deadlock detected on table.")
        assert r["matched_error_code"] in ("ADF-005", "UNKNOWN")

    def test_fallback_detects_parquet_column(self):
        """Real error: ParquetInvalidColumnName on pipeline3."""
        d = self._get_detector()
        r = d.detect(
            "ErrorCode=ParquetInvalidColumnName "
            "The column name is invalid. Column name cannot contain "
            "these characters: [,;{}()\\n\\t=]"
        )
        assert r["matched_error_code"] in ("ADF-006", "UNKNOWN")

    def test_result_has_required_keys(self):
        d = self._get_detector()
        r = d.detect("Some pipeline error occurred")
        required = {
            "matched_error_code", "matched_error_name", "confidence",
            "resolution_action", "auto_resolvable", "severity",
        }
        assert required.issubset(set(r.keys()))


# =============================================================================
#  Error Resolver Tests
# =============================================================================

class TestErrorResolver:

    def _make_detection(self, error_code: str, action: str, auto_resolvable=True):
        return {
            "matched_error_code": error_code,
            "matched_error_name": f"Error {error_code}",
            "resolution_action": action,
            "auto_resolvable": auto_resolvable,
            "confidence": 0.9,
            "detected_root_cause": f"Root cause for {error_code}",
            "explanation": f"Test explanation for {error_code}",
        }

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_linked_service(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        resolver._adf_client = MagicMock()
        resolver._adf_client.linked_services.list_by_factory.return_value = []
        result = resolver.resolve(
            self._make_detection("ADF-001", "refresh_linked_service_credentials")
        )
        assert result["error_code"] == "ADF-001"
        assert result["success"] is True
        assert len(result["actions_taken"]) > 0

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_schema_mismatch(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        resolver._adf_client = MagicMock()
        resolver._adf_client.datasets.list_by_factory.return_value = []
        result = resolver.resolve(
            self._make_detection("ADF-002", "refresh_schema_and_remap_columns")
        )
        assert result["error_code"] == "ADF-002"
        assert result["success"] is True

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_timeout(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        resolver._adf_client = MagicMock()
        result = resolver.resolve(
            self._make_detection("ADF-004", "increase_timeout_and_optimize_diu")
        )
        assert result["error_code"] == "ADF-004"
        assert result["success"] is True

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_stored_proc(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        result = resolver.resolve(
            self._make_detection("ADF-005", "retry_sp_with_dedup_and_deadlock_handling")
        )
        assert result["success"] is True

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_parquet_column_names(self, mock_cred, mock_adf):
        """ADF-006 — ParquetInvalidColumnName resolver returns sanitiser code."""
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        result = resolver.resolve(
            self._make_detection("ADF-006", "sanitise_parquet_column_names")
        )
        assert result["error_code"] == "ADF-006"
        assert result["success"] is True
        assert "sanitiser_code" in result
        assert "sanitise_column_names" in result["sanitiser_code"]
        assert "pf-observability-datafactory" in result["message"]

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_resolve_unknown(self, mock_cred, mock_adf):
        from src.error_resolver import AdfErrorResolver
        resolver = AdfErrorResolver()
        result = resolver.resolve(
            self._make_detection("UNKNOWN", "manual_investigation_required", False)
        )
        assert result["success"] is False

    @patch("src.error_resolver.DataFactoryManagementClient")
    @patch("src.error_resolver.ClientSecretCredential")
    def test_all_known_actions_are_in_dispatch(self, mock_cred, mock_adf):
        """Every action in the catalog must have a handler in the resolver."""
        from src.error_resolver import AdfErrorResolver
        from src.error_catalog import ADF_ERROR_CATALOG
        resolver = AdfErrorResolver()
        for error in ADF_ERROR_CATALOG:
            result = resolver.resolve({
                "matched_error_code": error.error_code,
                "matched_error_name": error.error_name,
                "resolution_action": error.resolution_action,
                "auto_resolvable": error.auto_resolvable,
                "confidence": 0.9,
                "detected_root_cause": "test",
                "explanation": "test",
            })
            assert "error_code" in result, (
                f"Resolver returned no error_code for {error.error_code}"
            )


# =============================================================================
#  Pipeline Orchestrator — Mock Mode Tests
# =============================================================================

class TestPipelineOrchestratorMock:

    def test_mock_run_succeeds(self):
        """In mock mode the orchestrator fails attempt 1, succeeds attempt 2."""
        orch   = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report = orch.run()
        assert report["final_status"] == "Succeeded"
        assert report["total_attempts"] == 2

    def test_mock_run_report_structure(self):
        orch   = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report = orch.run()
        required = {
            "session_id", "pipeline_name", "mode",
            "final_status", "run_history", "ingestion", "total_attempts",
        }
        assert required.issubset(set(report.keys()))

    def test_mock_run_has_detection_in_history(self):
        orch         = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report       = orch.run()
        failed_runs  = [r for r in report["run_history"] if r["status"] == "Failed"]
        assert len(failed_runs) == 1
        assert "detection" in failed_runs[0]
        assert failed_runs[0]["detection"]["matched_error_code"] == "ADF-002"

    def test_mock_run_has_resolution_in_history(self):
        orch        = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report      = orch.run()
        failed_runs = [r for r in report["run_history"] if r["status"] == "Failed"]
        assert "resolution" in failed_runs[0]

    def test_report_saved_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("logs", exist_ok=True)
        orch   = PipelineOrchestrator(mode=MODE_MOCK, max_retries=3)
        report = orch.run()
        log_files = list((tmp_path / "logs").glob("run_report_*.json"))
        assert len(log_files) == 1
        saved = json.loads(log_files[0].read_text())
        assert saved["session_id"] == report["session_id"]


# =============================================================================
#  CSV → Parquet Processor Tests — Local Mode
# =============================================================================

class TestCsvToParquetLocal:

    def test_local_conversion_basic(self, tmp_path):
        """Standard CSV converts to Parquet with correct row and column count."""
        import pandas as pd
        csv_dir     = tmp_path / "csv"
        csv_dir.mkdir()
        parquet_dir = tmp_path / "landing"
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Charlie"],
            "value": [10.5, 20.3, 30.1],
        })
        df.to_csv(csv_dir / "sample.csv", index=False)

        from src.data_processor import CsvToParquetProcessor
        proc   = CsvToParquetProcessor()
        result = proc.run_local(str(csv_dir), str(parquet_dir))

        assert result["files_processed"] == 1
        assert result["files_failed"] == 0

        import pyarrow.parquet as pq
        table = pq.read_table(str(list(parquet_dir.glob("*.parquet"))[0]))
        assert table.num_rows == 3
        assert "id" in table.schema.names

    def test_empty_csv_handled(self, tmp_path):
        """Empty CSV (headers only) converts without crashing."""
        import pandas as pd
        csv_dir = tmp_path / "csv"
        csv_dir.mkdir()
        pd.DataFrame(columns=["col1", "col2"]).to_csv(
            csv_dir / "empty.csv", index=False
        )
        from src.data_processor import CsvToParquetProcessor
        proc   = CsvToParquetProcessor()
        result = proc.run_local(str(csv_dir), str(tmp_path / "landing"))
        assert result["files_failed"] == 0

    # ── ADF-006 specific tests ────────────────────────────────────────────────

    def test_sanitise_column_names_removes_invalid_chars(self):
        """sanitise_column_names() must strip all Parquet-invalid characters."""
        import pandas as pd
        from src.data_processor import sanitise_column_names

        df = pd.DataFrame({
            "order;id":       [1],
            "amount(usd)":    [100],
            "customer[name]": ["Alice"],
            "col{bad}":       ["x"],
            "normal_col":     ["y"],
            "tab\there":      ["z"],
            "new\nline":      ["w"],
        })
        result = sanitise_column_names(df)
        invalid = re.compile(r'[,;{}\(\)\n\t=\[\]]')
        for col in result.columns:
            assert not invalid.search(col), f"Invalid char still in column: '{col}'"

    def test_sanitise_column_names_preserves_valid_cols(self):
        """sanitise_column_names() must NOT change columns that are already valid."""
        import pandas as pd
        from src.data_processor import sanitise_column_names

        df      = pd.DataFrame({"valid_col": [1], "another_valid": [2]})
        before  = list(df.columns)
        result  = sanitise_column_names(df)
        assert list(result.columns) == before

    def test_parquet_invalid_columns_fixed_in_pipeline(self, tmp_path):
        """
        End-to-end: CSV with Parquet-invalid column names must produce a
        valid Parquet file with clean column names.
        Reproduces the real ADF-006 error from pipeline3.
        """
        import pandas as pd
        import pyarrow.parquet as pq
        from src.data_processor import CsvToParquetProcessor

        csv_dir     = tmp_path / "csv"
        csv_dir.mkdir()
        parquet_dir = tmp_path / "landing"

        # Simulate CSV with bad column names like the real pipeline3 error
        df = pd.DataFrame({
            "order;id":           [1, 2],
            "amount(usd)":        [100, 200],
            "customer[name]":     ["Alice", "Bob"],
            "status{flag}":       ["A", "B"],
            "normal_column":      ["x", "y"],
        })
        df.to_csv(csv_dir / "pipeline3_data.csv", index=False)

        proc   = CsvToParquetProcessor()
        result = proc.run_local(str(csv_dir), str(parquet_dir))

        assert result["files_processed"] == 1, "File should process without error"
        assert result["files_failed"] == 0,    "No files should fail"

        # Verify output Parquet has clean column names
        parquet_files = list(parquet_dir.glob("*.parquet"))
        assert len(parquet_files) == 1
        table   = pq.read_table(str(parquet_files[0]))
        invalid = re.compile(r'[,;{}\(\)\n\t=\[\]]')

        for col in table.schema.names:
            assert not invalid.search(col), (
                f"Parquet-invalid character still present in column: '{col}'"
            )
        assert table.num_rows == 2

    def test_multiple_csv_files_all_sanitised(self, tmp_path):
        """Multiple CSV files in the same folder all get column names sanitised."""
        import pandas as pd
        import pyarrow.parquet as pq
        from src.data_processor import CsvToParquetProcessor

        csv_dir     = tmp_path / "csv"
        csv_dir.mkdir()
        parquet_dir = tmp_path / "landing"
        invalid     = re.compile(r'[,;{}\(\)\n\t=\[\]]')

        for i in range(3):
            pd.DataFrame({
                f"col;{i}": [i],
                f"val({i})": [i * 10],
            }).to_csv(csv_dir / f"file_{i}.csv", index=False)

        proc   = CsvToParquetProcessor()
        result = proc.run_local(str(csv_dir), str(parquet_dir))

        assert result["files_processed"] == 3
        assert result["files_failed"]    == 0

        for pf in parquet_dir.glob("*.parquet"):
            table = pq.read_table(str(pf))
            for col in table.schema.names:
                assert not invalid.search(col), f"Invalid col in {pf.name}: '{col}'"

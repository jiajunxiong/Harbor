import { useCallback, useState } from "react";

import { downloadValidationReport } from "../../api/endpoints";
import { useApiInfo } from "../../api/hooks";
import { describeApiError } from "../../api/problem";
import { downloadFilename, saveBlob } from "../../download";

export interface ValidationReportExportProps {
  runId: string;
}

const FORMAT_LABELS: Record<string, string> = {
  json: "JSON",
  csv: "CSV",
  html: "HTML",
};

const FORMAT_HINTS: Record<string, string> = {
  json: "完整产物：冻结配置、数据集清单、试验日志、折叠结果、压力差异、结论与审计事件",
  csv: "多表 CSV（每段以 # 表名分隔），便于导入表格工具复核",
  html: "自包含研究用报告，含口径说明与免责声明",
};

/**
 * Report downloads for a validation run (MVP 5 / SP 5.34).
 *
 * The document is rendered by the server with the same renderer
 * `harbor-cli validation report` writes, so a download and a terminal report
 * cannot disagree — including about which sections the database could not
 * supply. The formats come from the API's capability document, so a button can
 * never offer something the API would refuse.
 */
export function ValidationReportExport({ runId }: ValidationReportExportProps) {
  const info = useApiInfo();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const formats = info.data?.validation_report_formats ?? [];

  const download = useCallback(
    async (reportFormat: string) => {
      setBusy(reportFormat);
      setError(null);
      try {
        const file = await downloadValidationReport(runId, reportFormat);
        saveBlob(
          file.blob,
          downloadFilename(file.filename, `harbor-validation-${runId}.${reportFormat}`),
        );
      } catch (cause) {
        setError(describeApiError(cause).detail);
      } finally {
        setBusy(null);
      }
    },
    [runId],
  );

  return (
    <>
      <p className="card__hint" data-testid="validation-report-export-note">
        报告由服务端渲染，口径与 <span className="mono">harbor-cli validation report</span>{" "}
        完全一致；浏览器只保存服务端返回的字节，不重算任何数字。报告中缺失的分区会是空分区，
        不会被补算。
      </p>

      {info.isPending ? <p className="card__hint">正在读取可用的导出格式…</p> : null}
      {info.isError ? (
        <p className="notice" data-testid="validation-report-format-error">
          无法读取可用导出格式，导出按钮暂不可用：{describeApiError(info.error).detail}
        </p>
      ) : null}

      {info.isSuccess && formats.length === 0 ? (
        <p className="notice">服务端未声明可导出的格式，因此不提供导出按钮。</p>
      ) : null}

      <div className="export-row">
        {formats.map((reportFormat) => (
          <button
            key={reportFormat}
            type="button"
            className="button button--primary"
            data-testid={`validation-export-${reportFormat}`}
            disabled={busy !== null}
            onClick={() => {
              void download(reportFormat);
            }}
          >
            {busy === reportFormat
              ? "生成中…"
              : `下载 ${FORMAT_LABELS[reportFormat] ?? reportFormat}`}
          </button>
        ))}
      </div>

      <ul className="summary-list">
        {formats.map((reportFormat) => (
          <li key={reportFormat}>
            <span className="mono">{reportFormat}</span>：{FORMAT_HINTS[reportFormat] ?? "报告文件"}
          </li>
        ))}
      </ul>

      {error !== null ? (
        <p className="notice" role="alert" data-testid="validation-report-export-error">
          导出失败：{error}
        </p>
      ) : null}
    </>
  );
}

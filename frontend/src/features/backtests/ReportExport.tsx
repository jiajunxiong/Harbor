import { useCallback, useState } from "react";

import { downloadReport } from "../../api/endpoints";
import { useApiInfo } from "../../api/hooks";
import { describeApiError } from "../../api/problem";
import { downloadFilename, saveBlob } from "../../download";

export interface ReportExportProps {
  runId: string;
}

const FORMAT_LABELS: Record<string, string> = {
  json: "JSON",
  csv: "CSV",
  html: "HTML",
};

const FORMAT_HINTS: Record<string, string> = {
  json: "完整结果产物：运行信息、配置快照、净值、成交与拒单",
  csv: "多表 CSV（每段以 # 表名分隔），便于导入表格工具",
  html: "自包含研究用报告，含口径说明与免责声明",
};

/**
 * Report downloads (MVP 5 / SP 5.22).
 *
 * Every document is rendered by the server with the same renderer the CLI uses,
 * so a file downloaded here and a report printed in the terminal cannot disagree
 * about a number. The browser does not recompute anything.
 *
 * The available formats come from the API's capability document rather than a
 * hardcoded list, so the buttons can never offer a download the API would reject.
 */
export function ReportExport({ runId }: ReportExportProps) {
  const info = useApiInfo();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const formats = info.data?.report_formats ?? [];

  const download = useCallback(
    async (reportFormat: string) => {
      setBusy(reportFormat);
      setError(null);
      try {
        const file = await downloadReport(runId, reportFormat);
        saveBlob(
          file.blob,
          downloadFilename(file.filename, `harbor-backtest-${runId}.${reportFormat}`),
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
      <p className="card__hint" data-testid="report-export-note">
        报告由服务端渲染，口径与 <span className="mono">harbor-cli backtest report</span>{" "}
        完全一致；下载仅保存服务端返回的字节，不在浏览器重算任何数字。
      </p>

      {info.isPending ? <p className="card__hint">正在读取可用的导出格式…</p> : null}
      {info.isError ? (
        <p className="notice" data-testid="report-export-format-error">
          无法读取可用导出格式，导出按钮暂不可用：{describeApiError(info.error).detail}
        </p>
      ) : null}

      <div className="export-row">
        {formats.map((reportFormat) => (
          <button
            key={reportFormat}
            type="button"
            className="button button--primary"
            data-testid={`export-${reportFormat}`}
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
        <p className="notice" role="alert" data-testid="report-export-error">
          导出失败：{error}
        </p>
      ) : null}
    </>
  );
}

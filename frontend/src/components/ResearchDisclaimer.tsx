/**
 * Research-nature and disclaimer component (MVP 5 / SP 5.10).
 *
 * MVP 5 acceptance requires the dashboard to state that it is a research tool,
 * that it is not investment advice, and that it does not promise future return
 * or drawdown. Centralising the wording here means every page renders the same
 * statements, and `ResearchDisclaimer.test.tsx` fails if the wording is ever
 * weakened or if promise-like language creeps in.
 */

export interface ResearchDisclaimerProps {
  /** `"compact"` for the app footer, `"full"` for page headers. */
  variant?: "full" | "compact";
}

const STATEMENTS: readonly string[] = [
  "本看板是研究工具，只呈现已落库的回测、样本外验证与模拟盘事实。",
  "不构成投资建议，也不表示未来收益或回撤；历史与模拟结果均不能推断未来表现。",
  "模拟盘按既定撮合与成本假设模拟，不代表真实成交；提高自动化程度属 MVP 6，须单独评审。",
];

const COMPACT_STATEMENT =
  "研究工具：只读展示已落库结果，不构成投资建议，不表示未来收益或回撤。";

/** A prominent, non-dismissible statement of the tool's research nature. */
export function ResearchDisclaimer({ variant = "full" }: ResearchDisclaimerProps) {
  const compact = variant === "compact";
  return (
    <section
      className={compact ? "disclaimer disclaimer--compact" : "disclaimer"}
      role="note"
      aria-label="研究性质与免责声明"
      data-testid="research-disclaimer"
    >
      <span className="disclaimer__marker" aria-hidden="true">
        ※
      </span>
      <div className="disclaimer__body">
        <span className="disclaimer__title">研究性质与免责声明</span>
        {compact ? (
          <span>{COMPACT_STATEMENT}</span>
        ) : (
          <ul className="disclaimer__list">
            {STATEMENTS.map((statement) => (
              <li key={statement}>{statement}</li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

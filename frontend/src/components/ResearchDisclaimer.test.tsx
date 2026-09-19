import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ResearchDisclaimer } from "./ResearchDisclaimer";

describe("ResearchDisclaimer", () => {
  it("states the research nature, the absence of advice and the absence of a promise", () => {
    render(<ResearchDisclaimer />);

    const text = screen.getByTestId("research-disclaimer").textContent ?? "";
    expect(text).toContain("研究工具");
    expect(text).toContain("不构成投资建议");
    expect(text).toContain("不表示未来收益或回撤");
  });

  it("says that simulated results are not real fills", () => {
    render(<ResearchDisclaimer />);
    const text = screen.getByTestId("research-disclaimer").textContent ?? "";
    expect(text).toContain("不代表真实成交");
  });

  it("never promises a return, a drawdown bound or a profit", () => {
    render(<ResearchDisclaimer />);
    const text = screen.getByTestId("research-disclaimer").textContent ?? "";

    for (const forbidden of ["保证", "承诺", "稳赚", "必赚", "无风险", "保本", "必盈"]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("carries the same warnings in the compact variant", () => {
    render(<ResearchDisclaimer variant="compact" />);
    const text = screen.getByTestId("research-disclaimer").textContent ?? "";
    expect(text).toContain("不构成投资建议");
    expect(text).toContain("不表示未来收益或回撤");
  });

  it("is exposed as a note so assistive technology announces it", () => {
    render(<ResearchDisclaimer />);
    expect(screen.getByRole("note", { name: "研究性质与免责声明" })).toBeInTheDocument();
  });
});

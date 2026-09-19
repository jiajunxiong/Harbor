import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders a completed run as a success tone", () => {
    render(<StatusBadge status="completed" />);
    expect(screen.getByText("completed").className).toContain("badge--ok");
  });

  it("renders a failed run as a danger tone", () => {
    render(<StatusBadge status="failed" />);
    expect(screen.getByText("failed").className).toContain("badge--danger");
  });

  it("marks an inconclusive verdict as evidence, not approval", () => {
    render(<StatusBadge status="INCONCLUSIVE" />);
    const badge = screen.getByText("INCONCLUSIVE");
    expect(badge.className).toContain("badge--warn");
    expect(screen.getByText(/证据不足/).textContent).toContain("≠ 通过");
  });

  it("does not colour a not-qualified verdict as an incident", () => {
    render(<StatusBadge status="NOT_QUALIFIED" />);
    expect(screen.getByText("NOT_QUALIFIED").className).toContain("badge--neutral");
  });

  it("accepts an explicit label and tone", () => {
    render(<StatusBadge status="anything" label="数据库不可用" tone="danger" />);
    const badge = screen.getByText("数据库不可用");
    expect(badge.className).toContain("badge--danger");
  });
});

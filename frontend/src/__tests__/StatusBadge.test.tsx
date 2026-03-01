/**
 * Tests for StatusBadge component.
 *
 * Covers all BackendStatus values including the new `migration_error` entry.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "../components/StatusBadge";

describe("StatusBadge", () => {
  it("renders 'Backend Running' for running status", () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByText("Backend Running")).toBeTruthy();
  });

  it("renders 'Connecting...' for connecting status", () => {
    render(<StatusBadge status="connecting" />);
    expect(screen.getByText("Connecting...")).toBeTruthy();
  });

  it("renders 'Backend Error' for error status", () => {
    render(<StatusBadge status="error" />);
    expect(screen.getByText("Backend Error")).toBeTruthy();
  });

  it("renders 'Migration Required' for migration_error status", () => {
    render(<StatusBadge status="migration_error" />);
    expect(screen.getByText("Migration Required")).toBeTruthy();
  });

  it("uses yellow color token for migration_error", () => {
    const { container } = render(<StatusBadge status="migration_error" />);
    // The outer span carries the color via inline style
    const span = container.querySelector("span");
    expect(span?.getAttribute("style")).toContain("var(--yellow)");
  });

  it("uses green color token for running", () => {
    const { container } = render(<StatusBadge status="running" />);
    const span = container.querySelector("span");
    expect(span?.getAttribute("style")).toContain("var(--green)");
  });

  it("uses red color token for error", () => {
    const { container } = render(<StatusBadge status="error" />);
    const span = container.querySelector("span");
    expect(span?.getAttribute("style")).toContain("var(--red)");
  });
});

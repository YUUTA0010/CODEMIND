import { render, screen } from "@testing-library/react";
import { Mark, StatusMark, Empty, fmt, statusKind } from "../components/ui";
import { ApiError, describeError, qs } from "../services/api";

describe("epistemic marks", () => {
  it("labels heuristics distinctly from facts", () => {
    render(<><Mark kind="heuristic">score 0.5</Mark><Mark kind="fact">indexed</Mark></>);
    expect(screen.getByText("score 0.5")).toHaveClass("heuristic");
    expect(screen.getByText("score 0.5")).toHaveAttribute("title", expect.stringContaining("not a proven cause"));
    expect(screen.getByText("indexed")).toHaveClass("fact");
  });
  it("maps verification statuses honestly", () => {
    expect(statusKind("TESTS_PASSED")).toBe("pass");
    expect(statusKind("TESTS_FAILED")).toBe("fail");
    expect(statusKind("VERIFICATION_INCOMPLETE")).toBe("warn");
    expect(statusKind("PATCH_GENERATED")).toBe("warn");
    render(<StatusMark s="TESTS_TIMED_OUT" />);
    expect(screen.getByText("tests timed out")).toHaveClass("fail");
  });
  it("shows empty states as empty, and missing numbers as a dash", () => {
    render(<Empty title="No repositories">add one</Empty>);
    expect(screen.getByText("No repositories")).toBeInTheDocument();
    expect(fmt(null)).toBe("–"); expect(fmt(NaN)).toBe("–"); expect(fmt(0.5, 2)).toBe("0.50");
  });
});

describe("api helpers", () => {
  it("builds query strings and drops empty values", () => {
    expect(qs({ a: "x", b: "", c: undefined, d: 0, e: false })).toBe("?a=x&d=0&e=false");
    expect(qs({})).toBe("");
  });
  it("formats validation errors with field paths", () => {
    const e = new ApiError(422, "validation_error", "Invalid request", [{ loc: ["body", "path"], msg: "Field required" }]);
    expect(describeError(e)).toContain("path: Field required");
  });
});

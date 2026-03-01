/**
 * Tests for the MigrationErrorModal component.
 *
 * Key behaviors verified:
 * - Shows the error detail from props.
 * - REST wipe succeeds → reloads the page.
 * - REST fail + IPC success → reloads the page.
 * - REST fail + IPC {success: false} → shows error, does NOT reload.
 * - REST fail + IPC {success: false, error: "msg"} → shows "msg" in UI.
 * - REST fail + IPC throws → shows fallback error, does NOT reload.
 * - Quit button calls window.close().
 * - Reset button is disabled while wiping.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MigrationErrorModal } from "../components/MigrationErrorModal";

// ---------------------------------------------------------------------------
// Module mock
// ---------------------------------------------------------------------------

vi.mock("../api", () => ({
  wipeData: vi.fn(),
}));

import * as apiModule from "../api";

// ---------------------------------------------------------------------------
// window stubs
// ---------------------------------------------------------------------------

const reloadSpy = vi.fn();
const closeSpy = vi.fn();

beforeEach(() => {
  Object.defineProperty(window, "location", {
    value: { reload: reloadSpy },
    writable: true,
    configurable: true,
  });
  Object.defineProperty(window, "close", {
    value: closeSpy,
    writable: true,
    configurable: true,
  });
  reloadSpy.mockClear();
  closeSpy.mockClear();
  vi.mocked(apiModule.wipeData).mockReset();
  // Clear any electronAPI stub
  (window as unknown as Record<string, unknown>).electronAPI = undefined;
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setup(error: string | null = "Migration step 1 failed: column missing") {
  return render(<MigrationErrorModal error={error} />);
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

describe("MigrationErrorModal rendering", () => {
  it("shows the error detail from props", () => {
    setup("Migration step 1 failed: column missing");
    expect(screen.getByText(/Migration step 1 failed/)).toBeTruthy();
  });

  it("renders 'Reset Local Data' and 'Quit' buttons", () => {
    setup("some error");
    expect(screen.getByText("Reset Local Data")).toBeTruthy();
    expect(screen.getByText("Quit")).toBeTruthy();
  });

  it("renders even with null error prop", () => {
    setup(null);
    expect(screen.getByText("Reset Local Data")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Quit button
// ---------------------------------------------------------------------------

describe("Quit button", () => {
  it("calls window.close()", () => {
    setup("err");
    fireEvent.click(screen.getByText("Quit"));
    expect(closeSpy).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// REST wipe succeeds
// ---------------------------------------------------------------------------

describe("wipe — REST succeeds", () => {
  it("reloads the page immediately after REST wipe", async () => {
    vi.mocked(apiModule.wipeData).mockResolvedValue(true);
    setup("err");
    fireEvent.click(screen.getByText("Reset Local Data"));
    await waitFor(() => expect(reloadSpy).toHaveBeenCalledOnce());
  });
});

// ---------------------------------------------------------------------------
// REST fails, IPC available
// ---------------------------------------------------------------------------

describe("wipe — REST fails, IPC available", () => {
  it("reloads after successful IPC wipe when REST fails", async () => {
    vi.mocked(apiModule.wipeData).mockResolvedValue(false);
    (window as unknown as Record<string, unknown>).electronAPI = {
      wipeUserData: vi.fn().mockResolvedValue({ success: true }),
    };

    setup("err");
    fireEvent.click(screen.getByText("Reset Local Data"));
    await waitFor(() => expect(reloadSpy).toHaveBeenCalledOnce());
  });

  it("shows error and does NOT reload when IPC returns success: false", async () => {
    vi.mocked(apiModule.wipeData).mockResolvedValue(false);
    (window as unknown as Record<string, unknown>).electronAPI = {
      wipeUserData: vi.fn().mockResolvedValue({ success: false }),
    };

    setup("err");
    fireEvent.click(screen.getByText("Reset Local Data"));
    await waitFor(() =>
      expect(screen.getByText(/Failed to reset data/)).toBeTruthy()
    );
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("shows IPC error detail when returned alongside success: false", async () => {
    vi.mocked(apiModule.wipeData).mockResolvedValue(false);
    (window as unknown as Record<string, unknown>).electronAPI = {
      wipeUserData: vi.fn().mockResolvedValue({
        success: false,
        error: "Permission denied when deleting user data folder",
      }),
    };

    setup("err");
    fireEvent.click(screen.getByText("Reset Local Data"));
    await waitFor(() =>
      expect(
        screen.getByText(/Permission denied when deleting user data folder/)
      ).toBeTruthy()
    );
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("shows fallback error and does NOT reload when IPC throws", async () => {
    vi.mocked(apiModule.wipeData).mockResolvedValue(false);
    (window as unknown as Record<string, unknown>).electronAPI = {
      wipeUserData: vi.fn().mockRejectedValue(new Error("IPC channel closed")),
    };

    setup("err");
    fireEvent.click(screen.getByText("Reset Local Data"));
    await waitFor(() =>
      expect(screen.getByText(/Failed to reset data/)).toBeTruthy()
    );
    expect(reloadSpy).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// REST fails, no IPC (web browser context)
// ---------------------------------------------------------------------------

describe("wipe — REST fails, no IPC", () => {
  it("shows error and does NOT reload when no electronAPI is available", async () => {
    vi.mocked(apiModule.wipeData).mockResolvedValue(false);
    (window as unknown as Record<string, unknown>).electronAPI = undefined;

    setup("err");
    fireEvent.click(screen.getByText("Reset Local Data"));
    await waitFor(() =>
      expect(screen.getByText(/Failed to reset data/)).toBeTruthy()
    );
    expect(reloadSpy).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Button disabled state during wipe
// ---------------------------------------------------------------------------

describe("button disabled state", () => {
  it("shows 'Resetting…' and disables Reset button while wipe is in flight", async () => {
    // Never-resolving promise so we can inspect the in-flight state
    vi.mocked(apiModule.wipeData).mockReturnValue(new Promise(() => {}));

    setup("err");
    const btn = screen.getByText("Reset Local Data");
    fireEvent.click(btn);

    // Button should immediately show pending state
    await waitFor(() =>
      expect(screen.getByText("Resetting…")).toBeTruthy()
    );
    expect(screen.getByText("Resetting…")).toHaveProperty("disabled", true);
  });
});

// Registers the jest-dom matchers (toBeInTheDocument, ...) with Vitest.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without globals here, so Testing Library's automatic cleanup is
// not registered for us: without this, every render would accumulate in the
// document and queries would also match elements from earlier tests.
afterEach(() => {
  cleanup();
});

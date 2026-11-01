// Runs before every test file. It adds matchers such as
// `expect(element).toBeInTheDocument()` and cleans the fake page after each test.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

import { describe, expect, it } from "vitest"
import { ApiError, errorLabel, isAccessDenial, isAbort } from "@/lib/errors"

describe("dashboard error mapping", () => {
  it("maps known dashboard codes to business Chinese text", () => {
    expect(errorLabel(new ApiError(401, "UNAUTHENTICATED", "A valid bearer..."))).toContain("查看身份已失效")
    expect(errorLabel(new ApiError(403, "FORBIDDEN", "Current authority..."))).toContain("无权")
    expect(errorLabel(new ApiError(404, "NOT_FOUND", "The requested record..."))).toContain("不可见")
    expect(errorLabel(new ApiError(503, "DASHBOARD_VIEWER_UNAVAILABLE", "viewer"))).toContain("查看身份未配置")
  })

  it("never returns the raw English server message as content", () => {
    const error = new ApiError(422, "INVALID_REQUEST", "Request does not match the governed API schema")
    const label = errorLabel(error)
    expect(label).not.toContain("governed API")
    expect(label).not.toMatch(/[A-Za-z]{6,}/)
  })

  it("treats revoked authority (403/404) and unavailable viewer as access denial", () => {
    expect(isAccessDenial(new ApiError(401, "UNAUTHENTICATED", ""))).toBe(true)
    expect(isAccessDenial(new ApiError(403, "FORBIDDEN", ""))).toBe(true)
    expect(isAccessDenial(new ApiError(404, "NOT_FOUND", ""))).toBe(true)
    expect(isAccessDenial(new ApiError(503, "DASHBOARD_VIEWER_UNAVAILABLE", ""))).toBe(true)
    expect(isAccessDenial(new ApiError(503, "EVIDENCE_UNAVAILABLE", ""))).toBe(false)
    expect(isAccessDenial(new Error("network"))).toBe(false)
  })

  it("recognizes aborts separately", () => {
    expect(isAbort(new DOMException("aborted", "AbortError"))).toBe(true)
    expect(isAbort(new Error("x"))).toBe(false)
  })
})

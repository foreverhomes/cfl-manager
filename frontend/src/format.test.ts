import { describe, expect, it } from "vitest";
import { fmtCat, fmtMoney, fmtNum, fmtSigned, ordinal } from "./format";

describe("ordinal", () => {
  it.each([
    [1, "1st"],
    [2, "2nd"],
    [3, "3rd"],
    [4, "4th"],
    [11, "11th"],
    [12, "12th"],
    [13, "13th"],
    [21, "21st"],
    [102, "102nd"],
    [111, "111th"],
    [null, "–"],
  ])("%s is %s", (n, text) => {
    expect(ordinal(n)).toBe(text);
  });
});

describe("fmtCat", () => {
  it.each([
    ["SV%", 0.915, ".915"],
    ["GAA", 2.456, "2.46"],
    ["G", 4.567, "4.57"],
    ["G", -3.5, "-3.50"],
    ["G", 12.34, "12.3"],
    ["G", null, "–"],
  ])("%s %s is %s", (cat, v, text) => {
    expect(fmtCat(cat, v)).toBe(text);
  });
});

describe("fmtNum", () => {
  it.each([
    [1.234, undefined, "1.2"],
    [1.234, 2, "1.23"],
    [7, 0, "7"],
    [null, 1, "–"],
  ])("%s to %s digits is %s", (v, digits, text) => {
    expect(fmtNum(v, digits)).toBe(text);
  });
});

describe("fmtMoney", () => {
  it.each([
    [4.5, "$4.5"],
    [4, "$4"],
    [3.96, "$4"],
    [-2.5, "−$2.5"],
    [null, "–"],
  ])("%s is %s", (v, text) => {
    expect(fmtMoney(v)).toBe(text);
  });
});

describe("fmtSigned", () => {
  it.each([
    [1.234, 2, "+1.23"],
    [-1.234, 2, "−1.23"],
    [2.4, 0, "+2"],
    [-0.004, 2, "0.00"],
    [Number.NaN, 2, "–"],
    [null, 2, "–"],
  ])("%s to %s digits is %s", (v, digits, text) => {
    expect(fmtSigned(v, digits)).toBe(text);
  });
});

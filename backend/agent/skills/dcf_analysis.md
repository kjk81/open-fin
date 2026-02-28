---
name: dcf_analysis
description: >
  Discounted Cash Flow (DCF) valuation playbook.  Gathers financial
  statements, computes free-cash-flow projections, applies a discount rate,
  and derives an intrinsic-value estimate for a given ticker.
required_tools:
  - get_financial_statements
  - get_balance_sheet
  - get_company_profile
---

# DCF Analysis Skill

## Objective
Estimate the intrinsic value of a company using a multi-stage Discounted Cash
Flow model, then compare it to the current market price.

## Steps

1. **Gather Inputs**
   - Fetch the last 4 annual income statements (`get_financial_statements`).
   - Fetch the last 4 annual balance sheets (`get_balance_sheet`).
   - Fetch the company profile for current market cap and sector context
     (`get_company_profile`).

2. **Compute Free Cash Flow (FCF)**
   - FCF = Operating Cash Flow − Capital Expenditures.
   - If CapEx is unavailable, approximate as 70 % of Depreciation & Amortisation.

3. **Project Future FCFs (5-year horizon)**
   - Use the compound annual growth rate (CAGR) of historical FCFs as the
     base growth rate, capped at 25 %.
   - Apply a linear fade toward a 3 % terminal growth rate over the
     projection period.

4. **Determine Discount Rate (WACC)**
   - Use the 10-year US Treasury yield as the risk-free rate.
   - Equity risk premium: 5.5 %.
   - Beta from the company profile (default 1.0 if missing).
   - Cost of debt: interest expense / total debt from the balance sheet.
   - WACC = weighted average of equity and debt costs.

5. **Terminal Value**
   - Gordon Growth Model: TV = FCF₅ × (1 + g) / (WACC − g), where g = 3 %.

6. **Intrinsic Value**
   - Discount projected FCFs and terminal value back to present.
   - Subtract net debt; divide by diluted shares outstanding.
   - Report per-share intrinsic value and margin of safety vs. current price.

7. **Summary**
   - Present a table: Year | Projected FCF | Discount Factor | PV(FCF).
   - State the terminal value, enterprise value, equity value, and per-share
     intrinsic value.
   - Conclude with a Buy / Hold / Overvalued signal based on margin of safety
     thresholds (> 25 % undervalued → Buy, < −10 % → Overvalued).

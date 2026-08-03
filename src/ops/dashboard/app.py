"""Internal dev dashboard (Streamlit) — spec: GLUE, capped effort; user UI is M45.

Run on the VPS: streamlit run src/ops/dashboard/app.py
Reads the same snapshot/audit sources as the cockpit gateway. Not unit-tested
(dev tool, no money path).
"""
import json

try:
    import streamlit as st
except ImportError:  # dev tool only — never a dependency of the trading path
    st = None


def render(snapshot: dict, audit_rows: list) -> None:
    st.set_page_config(page_title="Trading OS — Dev Dashboard", layout="wide")
    st.title("Trading OS — internal dashboard")
    cols = st.columns(4)
    cols[0].metric("P&L (day)", snapshot.get("pnl", 0))
    cols[1].metric("VaR95", snapshot.get("var_95", "n/a"))
    cols[2].metric("Halted", str(snapshot.get("halted", False)))
    cols[3].metric("Open positions", snapshot.get("positions", 0))
    st.subheader("Last audit rows")
    st.dataframe(audit_rows[-10:])


if __name__ == "__main__" and st is not None:
    render({"pnl": 0, "var_95": "n/a", "halted": False, "positions": 0}, [])

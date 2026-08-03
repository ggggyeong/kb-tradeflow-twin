from __future__ import annotations

from typing import Any

import streamlit as st


def setup_page(title: str, icon: str, *, chat_mode: bool = False) -> None:
    st.set_page_config(
        page_title=f"{title} · KB TradeFlow Twin",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="collapsed" if chat_mode else "expanded",
    )
    chat_css = (
        """
        [data-testid="stAppViewContainer"] { background:#F3F0EA; }
        [data-testid="stHeader"] { background:transparent; }
        .block-container {
          max-width:820px; height:max-content !important; min-height:calc(100vh - 32px);
          flex:0 0 auto !important; margin:16px auto;
          padding:0 28px 28px; background:#FFFFFF; border:1px solid #DED9D0;
          border-radius:26px; box-shadow:0 22px 70px rgba(61,55,48,.15);
          overflow:visible;
        }
        .kb-chat-header {
          display:flex; align-items:center; justify-content:space-between; gap:18px;
          margin:0 -28px 26px; padding:19px 26px;
          color:#FFFFFF; background:linear-gradient(135deg,#60584C,#37332E);
          border-bottom:4px solid #FFBC00; border-radius:25px 25px 0 0;
        }
        .kb-chat-brand { display:flex; align-items:center; gap:12px; }
        .kb-chat-mark {
          width:54px; height:42px; display:grid; place-items:center; flex:0 0 54px;
          padding:5px 7px; border-radius:12px; background:rgba(255,255,255,.09);
          border:1px solid rgba(255,188,0,.34);
        }
        .kb-chat-mark img { width:100%; height:100%; object-fit:contain; display:block; }
        .kb-chat-title { color:#FFFFFF; font-size:1.06rem; line-height:1.15; font-weight:800; }
        .kb-chat-subtitle { margin-top:4px; color:rgba(255,255,255,.78); font-size:.78rem; }
        .kb-online {
          display:inline-flex; align-items:center; gap:7px; white-space:nowrap;
          padding:7px 11px; border-radius:999px; background:rgba(255,255,255,.1);
          color:#FFFFFF; font-size:.74rem; font-weight:700;
        }
        .kb-online::before {
          content:""; width:7px; height:7px; border-radius:50%; background:#FFCC00;
          box-shadow:0 0 0 4px rgba(255,204,0,.15);
        }
        .kb-graph-status {
          display:inline-flex; align-items:center; gap:8px; min-height:32px;
          padding:6px 11px; border-radius:999px; font-size:.79rem; font-weight:800;
        }
        .kb-graph-connected { color:#17663A; background:#EAF7EF; }
        .kb-graph-disconnected { color:#A23B37; background:#FCEDEC; }
        .kb-graph-dot {
          width:8px; height:8px; border-radius:50%; background:currentColor;
          box-shadow:0 0 0 4px rgba(23,102,58,.1);
        }
        .kb-graph-disconnected .kb-graph-dot {
          box-shadow:0 0 0 4px rgba(162,59,55,.1);
        }
        .kb-welcome { text-align:center; padding:18px 10px 24px; }
        .kb-welcome-orb {
          width:58px; height:58px; display:grid; place-items:center; margin:0 auto 15px;
          border-radius:19px; color:#2D2A26; font-size:1rem; font-weight:850;
          background:linear-gradient(135deg,#FFCC00,#FFBC00);
          box-shadow:0 12px 28px rgba(255,188,0,.25);
        }
        .kb-welcome h1 { margin:0; color:#182033 !important; font-size:1.72rem; }
        .kb-welcome p { max-width:520px; margin:9px auto 0; color:#667085; font-size:.95rem; }
        .kb-section-label {
          margin:4px 0 10px; color:#98A0AF; font-size:.72rem; font-weight:800;
          letter-spacing:.11em; text-transform:uppercase;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
          border-color:#E3DED5; border-radius:16px; background:#FFFFFF;
          box-shadow:0 5px 18px rgba(61,55,48,.05);
        }
        div[data-testid="stButton"] > button {
          min-height:45px; border-radius:13px; border:1px solid #DCD6CC;
          color:#3D3933; background:#FFFFFF; font-weight:720;
          box-shadow:none; transition:all .16s ease;
        }
        div[data-testid="stButton"] > button:hover {
          color:#2D2A26; border-color:#FFBC00; background:#FFF9E6;
          transform:translateY(-1px); box-shadow:0 8px 20px rgba(255,188,0,.14);
        }
        div[data-testid="stButton"] > button[kind="primary"] {
          color:#2D2A26; border-color:#E7AA00;
          background:linear-gradient(135deg,#FFCC00,#FFBC00);
          box-shadow:0 8px 18px rgba(255,188,0,.25);
        }
        [data-testid="stChatMessage"] {
          display:flex !important; align-items:flex-start !important;
          justify-content:flex-start !important; gap:.65rem !important;
          width:100%; max-width:100%; margin:.6rem 0; padding:0;
          border:0; background:transparent; box-shadow:none;
        }
        [data-testid="stChatMessageContent"] {
          flex:0 1 auto !important; width:fit-content !important; max-width:82%;
          margin:0 !important; padding:.82rem 1rem; overflow-wrap:anywhere;
          border-radius:18px 18px 18px 6px; background:#F0EEE9;
          box-shadow:0 2px 8px rgba(61,55,48,.04);
        }
        [data-testid="stChatMessage"]:has(
          > [data-testid="stChatMessageContent"][aria-label="Chat message from assistant"]
        ) { flex-direction:row !important; }
        [data-testid="stChatMessage"]:has(
          > [data-testid="stChatMessageContent"][aria-label="Chat message from user"]
        ) { flex-direction:row-reverse !important; justify-content:flex-start !important; }
        [data-testid="stChatMessage"]:has(
          > [data-testid="stChatMessageContent"][aria-label="Chat message from user"]
        ) [data-testid="stChatMessageContent"] {
          border-radius:18px 18px 6px 18px; color:#2D2A26;
          background:linear-gradient(135deg,#FFCC00,#FFBC00);
          box-shadow:0 8px 18px rgba(255,188,0,.2);
        }
        [data-testid="stChatMessage"]:has(
          > [data-testid="stChatMessageContent"][aria-label="Chat message from user"]
        ) [data-testid="stChatMessageContent"] * { color:#2D2A26 !important; }
        [data-testid="stChatMessage"] p { line-height:1.58; }
        [data-testid="stChatMessage"] img { border-radius:10px; }
        [data-testid="stChatInput"] {
          border:1px solid #D8D1C6; border-radius:18px; background:#FFFFFF;
          box-shadow:0 10px 30px rgba(61,55,48,.12);
        }
        .st-key-chat-composer-shell {
          margin-top:18px; padding-top:14px; border-top:1px solid #EEE7DD;
          background:#FFFFFF;
        }
        [data-testid="stFileUploaderDropzone"] {
          border:1px dashed #B8AA96; border-radius:14px; background:#FFFCF5;
        }
        [data-testid="stExpander"] {
          border-color:#E3DED5; border-radius:14px; background:#FAF8F4;
        }
        .kb-inline-note {
          padding:12px 14px; border:1px solid #E0D8CB; border-radius:14px;
          color:#60584C; background:#FAF7F1; font-size:.88rem;
        }
        @media (max-width:760px) {
          .block-container { min-height:100vh; margin:0; padding:0 16px 24px;
            border:0; border-radius:0; box-shadow:none; }
          .kb-chat-header { margin:0 -16px 18px; padding:16px;
            border-radius:0; }
          .kb-online { display:none; }
          [data-testid="stChatMessageContent"] { max-width:88%; }
        }
        """
        if chat_mode
        else ""
    )
    base_css = """
        :root { --navy:#3C3833; --teal:#FFBC00; --ink:#3A3631; --mist:#F5F2EC; }
        .stApp { background:
          radial-gradient(circle at 95% 0%, rgba(255,188,0,.14), transparent 28rem),
          linear-gradient(180deg,#FCFBF8 0%,#F4F1EB 100%); color:var(--ink); }
        [data-testid="stSidebar"] { background:var(--navy); }
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] a { color:#FFF9E8 !important; }
        h1,h2,h3 { letter-spacing:-.035em; color:var(--navy); }
        div[data-testid="stMetric"] {
          background:white; border:1px solid #E0D8CB; border-radius:16px;
          padding:14px 16px; box-shadow:0 8px 24px rgba(61,55,48,.05);
        }
        .kb-kicker { color:var(--teal); font-weight:700; font-size:.78rem;
          letter-spacing:.14em; text-transform:uppercase; margin-bottom:.2rem; }
        .kb-hero { background:linear-gradient(130deg,#60584C,#302D29);
          border-radius:22px; padding:28px 32px; margin:2px 0 24px;
          border-bottom:4px solid #FFBC00;
          box-shadow:0 18px 50px rgba(61,55,48,.18); overflow:hidden; }
        .kb-hero h1,.kb-hero p { color:white !important; margin:0;
          max-width:100%; overflow-wrap:anywhere; }
        .kb-hero h1 { font-size:clamp(1.9rem,3.2vw,3rem); line-height:1.08; }
        .kb-hero p { margin-top:7px; opacity:.78; }
        .kb-chip { display:inline-block; border-radius:999px; padding:5px 10px;
          margin:2px 4px 2px 0; background:#FFF2BF; color:#60584C;
          font-size:.76rem; font-weight:700; }
        .kb-card { background:white; border:1px solid #E0D8CB; border-radius:16px;
          padding:18px; margin:8px 0; box-shadow:0 8px 24px rgba(61,55,48,.05); }
    """
    st.markdown(
        "<style>" + base_css + chat_css + "</style>",
        unsafe_allow_html=True,
    )
    if not chat_mode:
        st.sidebar.markdown("### KB TradeFlow Twin")
        st.sidebar.caption("Agentic Trade Operations · v16.0")


def hero(title: str, subtitle: str, kicker: str) -> None:
    st.markdown(
        f"""
        <div class="kb-hero">
          <div class="kb-kicker">{kicker}</div>
          <h1>{title}</h1><p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def api_guard(call: Any) -> Any:
    try:
        return call()
    except Exception as exc:
        st.error(f"FastAPI 연결 또는 처리 오류: {exc}")
        return None

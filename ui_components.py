import streamlit as st


def render_app_style(is_light_mode=False):
    bg_col = "#ffffff" if is_light_mode else "#0b1120"
    border_col = "#ddd" if is_light_mode else "#1e293b"
    app_bg = "#f4f6f9" if is_light_mode else "#0b1120"
    panel_bg = "#0f172a"
    panel_border = "#1e293b"
    muted_text = "#64748b"
    soft_text = "#94a3b8"

    st.markdown(
        f"""
<style>
    .stApp {{ background-color: {app_bg}; overflow-x: hidden; }}
    #MainMenu {{visibility: hidden;}} footer {{visibility: hidden;}}
    [data-testid="collapsedControl"] {{ border: 1px solid {border_col} !important; border-radius: 8px !important; background-color: {bg_col} !important; padding: 5px 12px !important; display: flex !important; align-items: center !important; width: auto !important; transition: 0.3s; z-index: 1000; }}
    [data-testid="collapsedControl"]::after {{ content: " ⭐ 我的群組"; font-size: 1.1rem; font-weight: bold; color: #ffcc00; margin-left: 8px; }}
    a.stock-card-link {{ text-decoration: none; color: inherit; display: block; }}
    .radar-panel {{ background-color: {panel_bg}; border: 1px solid {panel_border}; border-radius: 12px; padding: 14px; margin-bottom: 12px; color: #e2e8f0; }}
    .radar-metric {{ background-color: {panel_bg}; border: 1px solid {panel_border}; border-radius: 12px; padding: 16px; text-align: center; color: #e2e8f0; min-height: 92px; }}
    .radar-label {{ color: {soft_text}; font-size: 0.85rem; font-weight: 700; }}
    .radar-subtle {{ color: {muted_text}; }}
    .terminal-card {{ background:#0F172A; border:1px solid #1E293B; border-radius:10px; padding:14px; color:#E2E8F0; }}
    .terminal-title {{ color:#94A3B8; font-size:0.78rem; font-weight:800; letter-spacing:0; }}
    .terminal-value {{ font-size:1.35rem; font-weight:900; line-height:1.25; }}
    .terminal-sub {{ color:#94A3B8; font-size:0.78rem; font-weight:700; }}
    .section-title {{ color:#E2E8F0; font-size:1.05rem; font-weight:900; margin:0 0 10px 0; }}
    .hero-panel {{ background:#0F172A; border:1px solid #1E293B; border-radius:12px; padding:20px; margin-bottom:16px; }}
    .market-status-grid {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:12px; margin:10px 0 12px 0; }}
    .market-status-card {{ min-height:96px; }}
    .chart-control-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:10px 0 14px 0; }}
    .chart-control-card {{ display:flex; align-items:center; justify-content:center; min-height:48px; padding:10px 8px; border-radius:10px; border:1px solid #1E293B; background:#0F172A; color:#CBD5E1; text-decoration:none !important; font-weight:850; text-align:center; }}
    .chart-control-card.active {{ border-color:#60A5FA; background:rgba(96,165,250,.12); color:#E2E8F0; box-shadow:0 0 0 1px rgba(96,165,250,.60), 0 0 16px rgba(96,165,250,.16); }}
    .chart-control-card.off {{ color:#64748B; }}
    .metric-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:12px 0 16px 0; }}
    div[role="radiogroup"] {{ gap:10px; }}
    div[role="radiogroup"] label {{
        position:relative;
        min-height:44px;
        background:#111827 !important;
        border:1px solid #1E293B !important;
        border-radius:8px !important;
        padding:10px 16px !important;
        margin-right:6px;
        color:#E2E8F0 !important;
        font-weight:800;
        transition:border-color .18s ease, box-shadow .18s ease, background .18s ease;
    }}
    div[role="radiogroup"] label > div:first-child,
    div[role="radiogroup"] label input[type="radio"] {{
        display:none !important;
        opacity:0 !important;
        width:0 !important;
        height:0 !important;
        margin:0 !important;
        padding:0 !important;
    }}
    div[role="radiogroup"] label > div:first-child *,
    div[role="radiogroup"] label span[role="radio"],
    div[role="radiogroup"] label div[role="radio"],
    div[role="radiogroup"] label [data-testid="stRadioIcon"] {{
        display:none !important;
        opacity:0 !important;
        width:0 !important;
        height:0 !important;
        margin:0 !important;
        padding:0 !important;
    }}
    div[data-testid="stRadio"] [data-baseweb="radio"] > div:first-child,
    div[data-testid="stRadio"] [data-baseweb="radio"] > div:first-child *,
    div[data-testid="stRadio"] [data-baseweb="radio"] svg,
    div[data-testid="stRadio"] [data-baseweb="radio"] [aria-hidden="true"],
    div[role="radiogroup"] svg,
    div[role="radiogroup"] label::before,
    div[role="radiogroup"] label::after {{
        display:none !important;
        opacity:0 !important;
        width:0 !important;
        height:0 !important;
        margin:0 !important;
        padding:0 !important;
        border:0 !important;
    }}
    div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] {{
        display:block !important;
        opacity:1 !important;
        width:auto !important;
        height:auto !important;
        margin:0 !important;
        color:#E2E8F0 !important;
    }}
    div[role="radiogroup"] label:has(input:checked) {{
        border-color:#60A5FA !important;
        background:rgba(96,165,250,0.12) !important;
        box-shadow:0 0 0 1px rgba(96,165,250,.65), 0 0 18px rgba(96,165,250,.18) !important;
    }}
    div[role="radiogroup"] label:hover {{
        border-color:#3B82F6 !important;
        background:rgba(96,165,250,0.08) !important;
    }}
    [data-testid="stToggle"] label {{
        display:inline-flex !important;
        align-items:center !important;
        min-height:44px;
        background:#111827 !important;
        border:1px solid #1E293B !important;
        border-radius:8px !important;
        padding:10px 16px !important;
        color:#E2E8F0 !important;
        font-weight:800;
        transition:border-color .18s ease, box-shadow .18s ease, background .18s ease;
    }}
    [data-testid="stToggle"] label > div:first-child,
    [data-testid="stToggle"] input {{
        display:none !important;
        opacity:0 !important;
        width:0 !important;
        height:0 !important;
        margin:0 !important;
        padding:0 !important;
    }}
    [data-testid="stToggle"] label div[data-testid="stMarkdownContainer"] {{
        display:block !important;
        opacity:1 !important;
        width:auto !important;
        height:auto !important;
        margin:0 !important;
        color:#E2E8F0 !important;
    }}
    [data-testid="stToggle"] label:has(input:checked) {{
        border-color:#60A5FA !important;
        background:rgba(96,165,250,0.12) !important;
        box-shadow:0 0 0 1px rgba(96,165,250,.65), 0 0 18px rgba(96,165,250,.18) !important;
    }}
    [data-testid="stToggle"] label:hover {{
        border-color:#3B82F6 !important;
        background:rgba(96,165,250,0.08) !important;
    }}
    .stButton > button,
    div[data-testid="stButton"] button,
    button[kind="secondary"],
    button[kind="primary"] {{
        background:#0F172A !important;
        color:#E2E8F0 !important;
        border:1px solid #1E293B !important;
        border-radius:10px !important;
        min-height:42px;
        font-weight:850 !important;
        box-shadow:none !important;
        transition:border-color .18s ease, box-shadow .18s ease, background .18s ease, color .18s ease;
    }}
    .stButton > button:hover,
    div[data-testid="stButton"] button:hover,
    button[kind="secondary"]:hover,
    button[kind="primary"]:hover {{
        background:rgba(96,165,250,0.12) !important;
        border-color:#60A5FA !important;
        color:#E2E8F0 !important;
        box-shadow:0 0 0 1px rgba(96,165,250,.55), 0 0 16px rgba(96,165,250,.16) !important;
    }}
    .stButton > button:active,
    div[data-testid="stButton"] button:active,
    button[kind="secondary"]:active,
    button[kind="primary"]:active {{
        background:rgba(96,165,250,0.18) !important;
        border-color:#93C5FD !important;
        color:#E2E8F0 !important;
    }}
    .stTextInput input,
    .stNumberInput input,
    .stTextArea textarea,
    div[data-baseweb="select"] > div {{
        background:#0F172A !important;
        color:#E2E8F0 !important;
        border:1px solid #1E293B !important;
        border-radius:10px !important;
        box-shadow:none !important;
    }}
    .stTextInput input:focus,
    .stNumberInput input:focus,
    .stTextArea textarea:focus,
    div[data-baseweb="select"] > div:focus-within {{
        border-color:#60A5FA !important;
        box-shadow:0 0 0 1px rgba(96,165,250,.55), 0 0 16px rgba(96,165,250,.16) !important;
    }}
    @media (max-width: 900px) {{
        .market-status-grid {{ grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }}
        .metric-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
    }}
    @media (max-width: 520px) {{
        .market-status-grid {{ grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }}
        .market-status-card {{ min-height:82px; padding:10px 8px !important; border-radius:9px !important; }}
        .market-status-card .terminal-title {{ font-size:0.68rem; line-height:1.2; }}
        .market-status-card .terminal-value {{ font-size:1.02rem; line-height:1.2; word-break:break-word; }}
        .market-status-card .terminal-sub {{ font-size:0.66rem; line-height:1.2; }}
        .chart-control-grid {{ gap:7px; }}
        .chart-control-card {{ min-height:44px; font-size:.88rem; padding:8px 6px; }}
    }}
</style>
""",
        unsafe_allow_html=True,
    )


def fmt_num(value, digits=0, missing="--"):
    try:
        if value is None:
            return missing
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return missing


def change_color(value):
    try:
        return "#EF4444" if float(value) >= 0 else "#22C55E"
    except (TypeError, ValueError):
        return "#94A3B8"


def credibility_label(sample_count):
    try:
        n = int(sample_count)
    except (TypeError, ValueError):
        return "--", "#94A3B8"
    if n < 10:
        return "樣本嚴重不足", "#EF4444"
    if n < 30:
        return "僅供參考", "#FACC15"
    if n < 50:
        return "中等可信", "#60A5FA"
    return "統計較穩定", "#22C55E"


def render_market_status_cards(items):
    cards = []
    for item in items:
        color = item.get("color", "#E2E8F0")
        cards.append(
            f"""
<div class="terminal-card market-status-card">
  <div class="terminal-title">{item.get('label', '')}</div>
  <div class="terminal-value" style="color:{color};">{item.get('value', '--')}</div>
  <div class="terminal-sub" style="color:{color};">{item.get('sub', '')}</div>
</div>
"""
        )
    st.markdown(f"<div class='market-status-grid'>{''.join(cards)}</div>", unsafe_allow_html=True)


def render_home_side_panel(title, rows, empty_text="暫無資料"):
    st.markdown(f"<div class='section-title'>{title}</div>", unsafe_allow_html=True)
    if not rows:
        st.markdown(f"<div class='terminal-card terminal-sub'>{empty_text}</div>", unsafe_allow_html=True)
        return
    for row in rows[:6]:
        st.markdown(
            f"""
<div class="terminal-card" style="padding:10px; margin-bottom:8px;">
  <div style="display:flex; justify-content:space-between; gap:8px;">
    <b>{row.get('title', '')}</b>
    <span style="color:{row.get('color', '#94A3B8')}; font-weight:900;">{row.get('value', '')}</span>
  </div>
  <div class="terminal-sub">{row.get('sub', '')}</div>
</div>
""",
            unsafe_allow_html=True,
        )


def render_stock_hero(data, target, name, strategy_text):
    score = data.get("Score", 0)
    confidence = data.get("Confidence", 100)
    change = data.get("漲跌幅", 0)
    p_color = change_color(change)
    rating = str(data.get("評級", "觀察")).replace("🟢 ", "").replace("🟡 ", "").replace("⚪ ", "")
    st.markdown(
        f"""
<div class="hero-panel">
  <div style="display:flex; justify-content:space-between; gap:16px; align-items:flex-start; flex-wrap:wrap;">
    <div>
      <div style="font-size:1.8rem; font-weight:950; color:#E2E8F0;">{target} {name}</div>
      <div class="terminal-sub">{data.get('產業', '一般產業')}｜{data.get('Score_Mode', '盤後正式分數')}｜資料信心 {confidence}%</div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:1.9rem; font-weight:950; color:{p_color};">{data.get('收盤價', '--')} <span style="font-size:1rem;">{change:+.2f}%</span></div>
      <div style="color:#94A3B8; font-weight:800;">AI 評級：<span style="color:#EF4444;">{rating} {score} 分</span></div>
    </div>
  </div>
  <div style="margin-top:14px; padding:12px; border-radius:8px; background:rgba(30,41,59,0.55); border:1px solid rgba(51,65,85,0.7); color:#E2E8F0; font-weight:800;">
    建議策略：{strategy_text}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_metric_grid(metrics):
    html = "<div class='metric-grid'>"
    for metric in metrics:
        html += (
            "<div class='terminal-card'>"
            f"<div class='terminal-title'>{metric.get('label', '')}</div>"
            f"<div class='terminal-value' style='color:{metric.get('color', '#E2E8F0')};'>{metric.get('value', '--')}</div>"
            f"<div class='terminal-sub'>{metric.get('sub', '')}</div>"
            "</div>"
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def generate_cards_html(
    df_disp,
    is_intraday=False,
    favorite_set=None,
    simulated_set=None,
    normalize_ticker=str,
    get_stock_name=str,
    safe_num=float,
    is_realtime_score_record=None,
    score_mode_label="盤後正式分數",
):
    cards_html = ""
    favorite_set = favorite_set or set()
    simulated_set = simulated_set or set()
    is_realtime_score_record = is_realtime_score_record or (lambda record: False)

    for _, r in df_disp.iterrows():
        record = r.to_dict() if hasattr(r, "to_dict") else dict(r)
        p_val = record.get("漲跌幅", record.get("漲跌", 0))
        p_col = "#ef4444" if p_val >= 0 else "#22c55e"
        p_bg = "rgba(239,68,68,0.1)" if p_val >= 0 else "rgba(34,197,94,0.1)"
        change_sign = "+" if p_val > 0 else ""

        score = record.get("Score", 0)
        s_col = "#ef4444" if score >= 60 else ("#facc15" if score >= 45 else "#22c55e")
        rating = record.get("評級", "⚪ 忽略").replace("🟢 ", "").replace("🟡 ", "").replace("⚪ ", "")
        score_mode = record.get("Score_Mode", score_mode_label)
        score_source = record.get("Score_Source", "")
        list_tag = record.get("List_Tag", "")
        if list_tag == "嚴格起漲":
            tag_bg, tag_col, tag_text = "rgba(34,197,94,0.14)", "#4ade80", "嚴格起漲"
        elif list_tag == "備援觀察":
            tag_bg, tag_col, tag_text = "rgba(96,165,250,0.14)", "#60A5FA", "備援觀察"
        elif list_tag:
            tag_bg, tag_col, tag_text = "rgba(250,204,21,0.14)", "#FACC15", "條件不足"
        else:
            tag_bg, tag_col, tag_text = "", "", ""

        r_col = "#4ade80" if "強勢" in rating else ("#facc15" if "偏多" in rating else "#94a3b8")
        ticker_code = normalize_ticker(record.get("代號", ""))
        mode_param = "&mode=intraday" if is_intraday or is_realtime_score_record(record) else ""
        stock_link = f'href="/?stock={ticker_code}{mode_param}" target="_self"'

        disp_name = str(record.get("名稱", "")).strip()
        if not disp_name or disp_name == ticker_code or disp_name.isdigit():
            disp_name = get_stock_name(record.get("代號", ""))
        fav_mark = " ⭐" if ticker_code in favorite_set else ""
        sim_mark = " 🛒" if ticker_code in simulated_set else ""
        sample_count = record.get("Backtest_Samples", record.get("closed_signals", record.get("ClosedSignals", "--")))
        cred_text, cred_color = credibility_label(sample_count)
        main_signal = record.get("Feature", "一般狀態")
        rrr = record.get("RRR", 1.5)

        cards_html += "<div style='background-color: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 14px; margin-bottom: 12px; position: relative; overflow: hidden;'>"
        cards_html += "<div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; position: relative; z-index: 10;'>"
        cards_html += "<div style='display: flex; align-items: flex-start; gap: 12px;'>"
        cards_html += f"<a {stock_link} class='stock-card-link'>"
        cards_html += f"<div style='display:flex; align-items:center; gap:8px; flex-wrap:wrap;'><span class='stock-name-hover' style='color: #f8fafc; font-weight: 950; font-size: 1.12rem; transition: color 0.2s;'>{record.get('代號', '')} {disp_name}{fav_mark}{sim_mark}</span>"

        industry_name = record.get("產業", "一般產業")
        cards_html += f"<span style='font-size: 0.72rem; background-color: rgba(79,70,229,0.15); color: #818cf8; border: 1px solid rgba(79,70,229,0.3); padding: 2px 6px; border-radius: 4px; white-space: nowrap; font-weight: 700;'>{industry_name}</span></div>"
        if tag_text:
            cards_html += f"<div style='display:inline-flex; margin-top:6px; font-size:0.72rem; background:{tag_bg}; color:{tag_col}; border:1px solid rgba(148,163,184,0.18); padding:2px 7px; border-radius:4px; font-weight:900;'>{tag_text}</div>"
        cards_html += f"<div style='font-size: 0.86rem; color: #94A3B8; margin-top: 6px;'>收盤 <span style='font-size:1.18rem; color:#E2E8F0; font-weight:950; font-family:monospace;'>{record.get('收盤價', 0):.1f}</span>｜<span style='color:{p_col}; font-weight:900;'>{change_sign}{record.get('漲跌幅', 0)}%</span>｜點擊解析</div></a></div>"
        cards_html += f"<div style='text-align:right;'><div style='color:{s_col}; font-size:1.45rem; font-weight:950;'>{score}分</div><div style='color:{r_col}; font-size:0.82rem; font-weight:900;'>{rating}</div></div></div>"

        cards_html += f"<div style='font-size:0.84rem; color:#E2E8F0; font-weight:800; margin-bottom:9px;'>主訊號：{main_signal}</div>"

        wr_val = record.get("WinRate", 0.0)
        wr_col = "#ef4444" if wr_val >= 60 else ("#facc15" if wr_val >= 40 else "#22c55e")
        confidence_val = safe_num(record.get("Confidence"), 100)
        conf_col = "#4ade80" if confidence_val >= 80 else ("#facc15" if confidence_val >= 60 else "#94a3b8")
        w_net = record.get("Whale_Net", 0)
        w_col = "#ef4444" if w_net > 0 else ("#22c55e" if w_net < 0 else "#94a3b8")
        whale_str = f"+{w_net:,}" if w_net > 0 else f"{w_net:,}"

        cards_html += "<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; background-color: rgba(30,41,59,0.4); border: 1px solid rgba(51,65,85,0.5); padding: 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 10px; position: relative; z-index: 10;'>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>歷史勝率</span><span style='color: {wr_col}; font-weight: bold; font-family: monospace;'>{wr_val}%</span></div>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>樣本 / 可信度</span><span style='color: {cred_color}; font-weight: bold; font-family: monospace;'>{sample_count}｜{cred_text}</span></div>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>法人10日</span><span style='color: {w_col}; font-weight: bold; font-family: monospace;'>{whale_str}</span></div>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>RRR</span><span style='color: #60A5FA; font-weight: bold; font-family: monospace;'>1 : {rrr}</span></div></div>"
        source_text = f"{score_mode}｜{score_source}" if score_source else score_mode
        cards_html += f"<div style='font-size:0.72rem; color:#64748b; margin-top:6px;'>分數來源：{source_text}</div>"
        cards_html += "</div>"

    return cards_html

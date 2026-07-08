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
</style>
""",
        unsafe_allow_html=True,
    )


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
        p_val = record.get("漲跌", 0)
        p_col = "#ef4444" if p_val >= 0 else "#22c55e"
        p_bg = "rgba(239,68,68,0.1)" if p_val >= 0 else "rgba(34,197,94,0.1)"
        change_sign = "+" if p_val > 0 else ""

        score = record.get("Score", 0)
        s_col = "#ef4444" if score >= 60 else ("#facc15" if score >= 45 else "#22c55e")
        rating = record.get("評級", "⚪ 忽略").replace("🟢 ", "").replace("🟡 ", "").replace("⚪ ", "")
        score_mode = record.get("Score_Mode", score_mode_label)
        score_source = record.get("Score_Source", "")

        r_col = "#4ade80" if "強勢" in rating else ("#facc15" if "偏多" in rating else "#94a3b8")
        ticker_code = normalize_ticker(record.get("代號", ""))
        mode_param = "&mode=intraday" if is_intraday or is_realtime_score_record(record) else ""
        stock_link = f'href="/?stock={ticker_code}{mode_param}" target="_self"'

        disp_name = record.get("名稱", "")
        if not disp_name:
            disp_name = get_stock_name(record.get("代號", ""))
        fav_mark = " ⭐" if ticker_code in favorite_set else ""
        sim_mark = " 🛒" if ticker_code in simulated_set else ""

        cards_html += "<div style='background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 12px; position: relative; overflow: hidden;'>"
        cards_html += "<div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; position: relative; z-index: 10;'>"
        cards_html += "<div style='display: flex; align-items: center; gap: 12px;'>"
        cards_html += "<div style='width: 50px; height: 50px; border-radius: 50%; background: radial-gradient(circle, #1e293b 0%, #0b1120 100%); border: 1px solid #334155; display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: inset 0 2px 4px rgba(255,255,255,0.05), 0 4px 8px rgba(0,0,0,0.4);'>"
        cards_html += f"<span style='color: {s_col}; font-weight: 800; font-size: 1.2rem; line-height: 1;'>{score}</span>"
        cards_html += f"<span style='color: {r_col}; font-size: 0.65rem; font-weight: 800; margin-top: 2px;'>{rating}</span></div>"

        cards_html += f"<a {stock_link} class='stock-card-link'><div style='display: flex; align-items: center; gap: 6px;'>"
        cards_html += f"<span class='stock-name-hover' style='color: #f8fafc; font-weight: bold; font-size: 1.15rem; transition: color 0.2s;'>{disp_name}{fav_mark}{sim_mark}</span>"

        industry_name = record.get("產業", "一般產業")
        cards_html += f"<span style='font-size: 0.7rem; background-color: rgba(79,70,229,0.15); color: #818cf8; border: 1px solid rgba(79,70,229,0.3); padding: 2px 6px; border-radius: 4px; white-space: nowrap; font-weight: 600;'>🏷️ {industry_name}</span>"
        cards_html += f"</div><div style='font-size: 0.8rem; color: #64748b; margin-top: 4px; font-family: monospace;'>{record.get('代號', '')} <span style='color:#475569; font-size:0.7rem; margin-left:4px;'>(點擊解析)</span></div></a></div>"

        cards_html += f"<div style='text-align: right; flex-shrink: 0;'><div style='color: {p_col}; font-weight: 800; font-size: 1.2rem; font-family: monospace;'>{record.get('收盤價', 0):.1f}</div>"
        cards_html += f"<div style='background-color: {p_bg}; color: {p_col}; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; display: inline-block; font-weight: 800; font-family: monospace; margin-top: 4px;'>{change_sign}{record.get('漲跌幅', 0)}%</div></div></div>"

        wr_val = record.get("WinRate", 0.0)
        wr_col = "#ef4444" if wr_val >= 60 else ("#facc15" if wr_val >= 40 else "#22c55e")
        confidence_val = safe_num(record.get("Confidence"), 100)
        conf_col = "#4ade80" if confidence_val >= 80 else ("#facc15" if confidence_val >= 60 else "#94a3b8")
        w_net = record.get("Whale_Net", 0)
        w_col = "#ef4444" if w_net > 0 else ("#22c55e" if w_net < 0 else "#94a3b8")
        whale_str = f"+{w_net:,}" if w_net > 0 else f"{w_net:,}"

        cards_html += "<div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; background-color: rgba(30,41,59,0.4); border: 1px solid rgba(51,65,85,0.5); padding: 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 10px; position: relative; z-index: 10;'>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>歷史勝率</span><span style='color: {wr_col}; font-weight: bold; font-family: monospace;'>{wr_val}%</span></div>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>資料信心</span><span style='color: {conf_col}; font-weight: bold; font-family: monospace;'>{confidence_val:.0f}%</span></div>"
        cards_html += f"<div style='display: flex; flex-direction: column;'><span style='color: #64748b; margin-bottom: 4px;'>法人淨買</span><span style='color: {w_col}; font-weight: bold; font-family: monospace;'>{whale_str}</span></div></div>"
        cards_html += f"<div style='font-size: 0.75rem; color: #fbbf24; display: flex; align-items: flex-start; gap: 6px; position: relative; z-index: 10;'><span style='margin-top: 1px;'>⚡</span><span style='line-height: 1.4; font-weight: 500;'>進場特徵：{record.get('Feature', '一般')}</span></div>"
        source_text = f"{score_mode}｜{score_source}" if score_source else score_mode
        cards_html += f"<div style='font-size:0.72rem; color:#64748b; margin-top:6px;'>分數來源：{source_text}</div>"
        cards_html += "</div>"

    return cards_html

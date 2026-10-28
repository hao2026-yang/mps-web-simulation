# -*- coding: utf-8 -*-
"""零碳园区 MPS 排产推演系统 V3.0 Pro Max（Streamlit 界面）

运行：streamlit run agent_v3.py
依赖：streamlit / matplotlib / pandas / numpy

新增（相对 V2）
  · 6 台设备、新老代差、同一工序多台可选（FJSP），换型按材质矩阵
  · 5 种物料（含外购件、辅料）、4 半成品 + 3 成品网状 BOM
  · 人工标准工时与加班费、虚拟电厂 DR 事件与调峰补贴
  · 闭环 MRP II 报表：APP / MPS / MRP / CRP
  · 节点电价热力图、分时碳因子与电碳双轴图、6 设备负荷曲线、设备负载率
"""

import os
import sys

import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mps_core_v3 as C  # noqa: E402

st.set_page_config(page_title="MPS 排产推演 V3.0 Pro Max", layout="wide")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                                   "WenQuanYi Zen Hei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

PCOLOR = {"P1": "#4C78A8", "P2": "#F58518", "P3": "#54A24B", "P4": "#B279A2",
          "FA": "#2F4B7C", "FB": "#E45756", "FC": "#72B7B2"}
NCOLOR = {"N1": "#4C78A8", "N2": "#E45756", "N3": "#54A24B"}
DCOLOR = {"A1": "#8C6D31", "A2": "#F2A93B", "B1": "#4C78A8", "B2": "#72B7B2",
          "C1": "#E45756", "C2": "#B279A2"}

st.title("零碳园区 MPS 工序级排产多方案推演系统 · V3.0 Pro Max")
st.caption("6 设备 FJSP ｜ 网状 BOM ｜ 换型矩阵 ｜ 节点电价 ｜ 分时碳因子 ｜ 虚拟电厂 DR ｜ 闭环 MRP II")


# --------------------------------------------------------------------- 侧边栏
def sidebar():
    with st.sidebar:
        st.header("📦 订单与生产结构")
        c1, c2 = st.columns(2)
        p1 = c1.number_input("P1 钢制半成品", 0, 60, 10, 1)
        p2 = c2.number_input("P2 铝制半成品", 0, 60, 8, 1)
        p3 = c1.number_input("P3 精密半成品", 0, 60, 6, 1)
        p4 = c2.number_input("P4 转子组件", 0, 60, 6, 1)
        fa = c1.number_input("FA 成品", 0, 40, 4, 1)
        fb = c2.number_input("FB 成品", 0, 40, 3, 1)
        fc = st.number_input("FC 成品", 0, 40, 2, 1)
        batch = st.slider("成品最小投产批量", 1, 5, 2)
        with st.expander("BOM / 工艺 / 设备 / 换型矩阵"):
            st.dataframe(pd.DataFrame(C.bom_table()), hide_index=True)
            st.dataframe(pd.DataFrame(C.device_table()), hide_index=True)
            st.dataframe(pd.DataFrame(C.change_matrix_rows()), hide_index=True)

        st.header("🚚 物料与提前期")
        lead_scale = st.slider("采购提前期系数", 0.5, 2.0, 1.0, 0.1)
        init_scale = st.slider("期初库存系数", 0.5, 2.0, 1.0, 0.1)
        with st.expander("物料主数据"):
            st.dataframe(pd.DataFrame(C.material_table()), hide_index=True)

        st.header("🏭 设备与人工")
        workers = st.slider("可用工人数", 1, 10, 3)
        wage = st.number_input("标准时薪(元)", 0, 200, 22, 1)
        overtime = st.slider("加班费率倍数", 1.0, 3.0, 1.5, 0.1)
        fault = st.slider("设备每步故障概率", 0.0, 0.10, 0.02, 0.01)
        ramp = st.slider("功率爬坡限值(kW/步)", 10, 100, 25, 5)

        st.header("🔌 节点电价")
        congestion = st.slider("节点阻塞价差系数", 0.0, 2.0, 1.0, 0.1)

        st.header("🌿 分时碳因子")
        carbon_base = st.slider("夜间碳因子(kg/kWh)", 0.30, 1.00, 0.65, 0.05)
        carbon_price = st.slider("碳价(元/t)", 20, 300, 60, 10)

        st.header("⚡ 虚拟电厂（DR）")
        dr_on = st.checkbox("启用需求响应事件", True)
        dr_decl = st.slider("日前申报基线(kW)", 20, 80, 45, 5)
        dr_target = st.slider("压降目标(kW)", 2, 30, 8, 1)
        dr_sub = st.slider("调峰补贴(元/kW·h)", 0, 20, 6, 1)

        st.header("🚨 紧急订单")
        presets = C.urgent_presets()
        urgent = presets[st.selectbox("急单方案", list(presets.keys()), 0)]
        if st.checkbox("自定义急单", False):
            u1, u2 = st.columns(2)
            up = u1.selectbox("产品", list(C.FINALS), 0)
            uq = u2.number_input("数量", 1, 10, 2, 1)
            ua = st.slider("到达(h)", 0.0, 23.0, 12.0, 0.5)
            ud = st.slider("交期(h)", 1.0, 24.0, 18.0, 0.5)
            urgent = C.UrgentOrder(up, int(uq), float(ua), float(ud), 60, f"自定义{up}×{int(uq)}")

        st.header("🎛 质量与开关")
        defect = st.slider("白班次品率", 0.0, 0.10, 0.02, 0.01)
        night_inc = st.slider("夜班次品率增量", 0.0, 0.05, 0.02, 0.01)
        rework = st.number_input("单件返工成本(元)", 0, 200, 16, 1)
        w_ele = st.slider("电费权重 w_ele", 0.0, 1.0, 0.6, 0.1)
        st.info(f"碳排权重 w_carbon = {round(1 - w_ele, 2)}")
        use_pv = st.checkbox("启用光伏", True)
        use_storage = st.checkbox("启用储能", True)
        use_flex = st.checkbox("允许工序柔性调度", True)

        st.markdown("---")
        b1 = st.button("▶ 运行当前场景（双方案）", use_container_width=True)
        b2 = st.button("🚨 对比 5 种急单方案", use_container_width=True)
        b3 = st.button("📊 四场景控制变量实验", use_container_width=True)
        b4 = st.button("📈 帕累托前沿", use_container_width=True)
        b5 = st.button("🎮 碳价博弈推演", use_container_width=True)

    p = C.Params(
        plans={"P1": int(p1), "P2": int(p2), "P3": int(p3), "P4": int(p4),
               "FA": int(fa), "FB": int(fb), "FC": int(fc)},
        batch_size=int(batch), workers=int(workers), wage=float(wage),
        overtime_rate=float(overtime), fault_prob=float(fault), ramp_limit=float(ramp),
        congestion=float(congestion), carbon_base=float(carbon_base),
        carbon_price=float(carbon_price), lead_scale=float(lead_scale),
        init_scale=float(init_scale), base_defect=float(defect),
        night_defect_inc=float(night_inc), rework_cost=float(rework),
        w_ele=float(w_ele), use_pv=bool(use_pv), use_storage=bool(use_storage),
        use_flex=bool(use_flex), urgent_orders=(urgent,) if urgent else (),
        dr=C.DRParams(enabled=bool(dr_on), declared_kw=float(dr_decl),
                      target_kw=float(dr_target), subsidy=float(dr_sub)),
    )
    return p, dict(single=b1, urgent=b2, batch=b3, pareto=b4, game=b5)


# --------------------------------------------------------------------- 绘图
def fig_gantt(res, title):
    fig, ax = plt.subplots(figsize=(10, 3.6))
    rows = {d: i + 1 for i, d in enumerate(C.DEVICES)}
    for e in res.schedule:
        ax.barh(rows[e["Device"]], max(1, e["Duration"]), left=e["Start"],
                color=PCOLOR[e["Product"]],
                edgecolor="red" if e["Urgent"] else "#333",
                linewidth=1.8 if e["Urgent"] else 0.4,
                alpha=0.92 if e["Urgent"] else 0.82)
    for a, b in res.dr_events:
        ax.axvspan(a, b, color="#FFD166", alpha=0.25, zorder=0)
        ax.annotate("DR", (a, len(rows) + 0.55), color="#B8860B", fontsize=7)
    for s in res.urgent_stats:
        ax.axvline(s["arrive"], color="red", linestyle="--", linewidth=1.2)
        ax.axvline(s["due"], color="orange", linestyle=":", linewidth=1.0)
    ax.axvline(C.DUE_STEP, color="#444", linestyle="-.", linewidth=0.8, alpha=0.7)
    ax.set_yticks(list(rows.values()))
    ax.set_yticklabels([f"{d} {C.DEVICES[d].name}" for d in rows], fontsize=7.5)
    ax.set_xlim(0, C.T)
    ax.set_ylim(0.3, len(rows) + 0.9)
    ax.set_xlabel("时段（15 min/格）", fontsize=8)
    ax.set_title(title, fontsize=10)
    ax.tick_params(labelsize=7.5)
    handles = [plt.Rectangle((0, 0), 1, 1, color=PCOLOR[k]) for k in C.PRODUCTS]
    ax.legend(handles, list(C.PRODUCTS), ncol=7, fontsize=6.5, frameon=False,
              loc="upper center", bbox_to_anchor=(0.5, -0.22))
    fig.tight_layout()
    return fig


def fig_device_load(res):
    fig, ax = plt.subplots(figsize=(10, 2.8))
    for d in C.DEVICES:
        y = np.array([sum(e["Power"] for e in res.schedule
                          if e["Device"] == d and e["Start"] <= t < e["End"]) for t in range(C.T)])
        ax.plot(y, label=d, color=DCOLOR[d], linewidth=1.3)
    ax.set_xlim(0, C.T)
    ax.set_ylabel("kW")
    ax.set_title("6 台设备 24 h 功率曲线（叠加）")
    ax.legend(ncol=6, fontsize=7, frameon=False)
    fig.tight_layout()
    return fig


def fig_device_util(res):
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    ds = list(C.DEVICES)
    rates = [res.device_util[d]["rate"] * 100 for d in ds]
    colors = ["#E45756" if r > 95 else ("#F2A93B" if r > 80 else "#4C78A8") for r in rates]
    ax.bar(ds, rates, color=colors)
    for i, r in enumerate(rates):
        ax.text(i, r + 1, f"{r:.0f}%", ha="center", fontsize=7)
    ax.set_ylim(0, 110)
    ax.axhline(90, color="red", linestyle="--", linewidth=0.8)
    ax.set_ylabel("负载率 %")
    ax.set_title("设备负载率（>90% 标红）")
    fig.tight_layout()
    return fig


def fig_node_heat(res):
    fig, ax = plt.subplots(figsize=(10, 2.0))
    mat = np.vstack([res.prices[n] for n in C.NODES])
    im = ax.imshow(mat, aspect="auto", cmap="YlOrRd", extent=[0, C.T, len(C.NODES), 0])
    ax.set_yticks([i + 0.5 for i in range(len(C.NODES))])
    ax.set_yticklabels([f"{n} {C.NODES[n]}" for n in C.NODES], fontsize=8)
    ax.set_xlabel("时段（15 min/格）", fontsize=8)
    plt.colorbar(im, ax=ax, pad=0.01, fraction=0.03).set_label("元/kWh", fontsize=8)
    ax.set_title("节点电价热力图（横轴时间，纵轴节点）", fontsize=10)
    fig.tight_layout()
    return fig


def fig_energy_carbon(res):
    fig, ax = plt.subplots(figsize=(10, 2.5))
    ax.plot(res.base_price, color="#d62728", label="分时电价（元/kWh）")
    ax.set_ylabel("电价 元/kWh", color="#d62728")
    ax.tick_params(axis="y", colors="#d62728")
    ax2 = ax.twinx()
    ax2.plot(res.carbon_factor, color="#2E7D32", label="分时碳因子（kg/kWh）")
    ax2.set_ylabel("碳因子 kg/kWh", color="#2E7D32")
    ax2.tick_params(axis="y", colors="#2E7D32")
    ax.set_xlim(0, C.T)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, frameon=False, loc="upper right")
    ax.set_title("电碳双轴：分时电价 vs 分时碳因子")
    fig.tight_layout()
    return fig


def fig_load(res):
    fig, ax = plt.subplots(figsize=(10, 2.6))
    ax.stackplot(range(C.T), [res.node_load[n] for n in ("N1", "N2")],
                 labels=["N1 机加区", "N2 装配区"],
                 colors=[NCOLOR["N1"], NCOLOR["N2"]], alpha=0.55)
    ax.plot(res.grid_import, color="#111", linewidth=1.2, label="购电负荷")
    ax.fill_between(range(C.T), 0, res.pv, color="green", alpha=0.18, label="光伏出力")
    ax.axhline(C.PEAK_LIMIT, color="red", linestyle="-.", linewidth=0.9, label="需量阈值 80kW")
    for a, b in res.dr_events:
        ax.axvspan(a, b, color="#FFD166", alpha=0.25)
    ax.set_xlim(0, C.T)
    ax.set_ylabel("kW")
    ax.legend(fontsize=7, ncol=5, frameon=False)
    ax.set_title("节点负荷构成 / 购电 / 光伏（黄色为 DR 窗口）")
    fig.tight_layout()
    return fig


def fig_soc(res):
    fig, ax = plt.subplots(figsize=(5.2, 2.2))
    ax.plot(res.soc, color="orange")
    ax.set_ylim(0, 1)
    ax.set_xlim(0, C.T)
    ax.set_title("储能 SOC", fontsize=9)
    fig.tight_layout()
    return fig


def fig_inv(res):
    fig, ax = plt.subplots(figsize=(5.2, 2.2))
    for s in C.SEMI:
        ax.plot(res.inv[s], drawstyle="steps-post", label=s, color=PCOLOR[s], linewidth=1.1)
    ax.set_xlim(0, C.T)
    ax.legend(fontsize=7, ncol=4, frameon=False)
    ax.set_title("半成品库存（齐套）", fontsize=9)
    fig.tight_layout()
    return fig


def fig_material(res):
    fig, ax = plt.subplots(figsize=(10, 2.2))
    colors = {"M1": "#8C6D31", "M2": "#4C78A8", "M3": "#B279A2", "M4": "#E45756", "M5": "#54A24B"}
    for m in C.MATERIALS:
        ax.plot(res.mat_stock[m], drawstyle="steps-post", label=m, color=colors[m], linewidth=1.1)
    ax.set_xlim(0, C.T)
    ax.legend(fontsize=7, ncol=5, frameon=False)
    ax.set_title("原材料 / 外购件 / 辅料库存", fontsize=9)
    fig.tight_layout()
    return fig


def fig_dr(res):
    fig, ax = plt.subplots(figsize=(10, 2.4))
    ax.plot(res.total_load, color="#111", linewidth=1.2, label="实际生产负荷")
    ax.plot(res.grid_import, color="#777", linewidth=0.9, linestyle=":", label="购电负荷")
    ax.plot(res.baseline, color="#B8860B", linewidth=1.6, linestyle="--", label="日前申报基线")
    ax.fill_between(range(C.T), res.total_load, res.baseline,
                    where=~np.isnan(res.baseline), color="#FFD166", alpha=0.35,
                    label="响应压降")
    ax.set_xlim(0, C.T)
    ax.set_ylabel("kW")
    ax.legend(fontsize=7, frameon=False)
    ax.set_title("虚拟电厂：日前申报基线 vs 实际生产负荷")
    fig.tight_layout()
    return fig


def fig_crp(res):
    fig, ax = plt.subplots(figsize=(7.4, 2.6))
    ds = list(res.device_util)
    load = [res.device_util[d]["busy"] for d in ds]
    ax.bar(ds, [C.T] * len(ds), color="#E8E8E8", label="可用产能")
    ax.bar(ds, load, color=["#E45756" if l > 0.95 * C.T else "#4C78A8" for l in load],
           label="实际负荷")
    for i, l in enumerate(load):
        ax.text(i, l + 1, str(l), ha="center", fontsize=7)
    ax.set_ylabel("步")
    ax.set_title("CRP：设备可用产能 vs 实际负荷", fontsize=10)
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    return fig


def fig_radar(a, b):
    def score(r, other):
        return [100 * min(1.0, r.flex_save / 20.0) if r.flex_save > 0 else 45,
                100 * min(1.0, r.pv_selfuse),
                100 * (1 - min(1.0, r.delay_cost / max(0.15 * r.total_cost, 1))),
                100 * (1 - r.night_share),
                100 * min(1.0, other.total_cost / max(r.total_cost, 1e-6))]

    labels = ["时序柔性", "绿电消纳", "交付保障", "质量风险", "经济性"]
    va, vb = score(a, b), score(b, a)
    ang = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    ang += ang[:1]
    va += va[:1]
    vb += vb[:1]
    fig, ax = plt.subplots(figsize=(4.8, 4.4), subplot_kw=dict(polar=True))
    ax.plot(ang, va, "o-", color="#1f77b4", label="方案A 敏捷派")
    ax.fill(ang, va, color="#1f77b4", alpha=0.15)
    ax.plot(ang, vb, "o-", color="#2ca02c", label="方案B 统筹派")
    ax.fill(ang, vb, color="#2ca02c", alpha=0.15)
    ax.set_thetagrids(np.degrees(ang[:-1]), labels, fontsize=8)
    ax.set_ylim(0, 100)
    ax.legend(fontsize=7, loc="upper right", bbox_to_anchor=(1.25, 1.1), frameon=False)
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------- 结果展示
def show_single(p, a, b):
    st.subheader("🏆 核心指标")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("方案A 敏捷派 总成本", f"{a.total_cost:,.2f} 元")
    c2.metric("方案B 统筹派 总成本", f"{b.total_cost:,.2f} 元",
              delta=f"{b.total_cost - a.total_cost:,.2f}", delta_color="inverse")
    c3.metric("方案A 碳排", f"{a.carbon:.1f} tCO2")
    c4.metric("方案B 碳排", f"{b.carbon:.1f} tCO2",
              delta=f"{b.carbon - a.carbon:.1f}", delta_color="inverse")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("方案B 物料", f"{b.mat_cost:,.0f} 元")
    c6.metric("方案B 人工/加班", f"{b.labor_cost:,.0f} 元（{b.overtime_h:.1f} h 加班）")
    c7.metric("方案B 换产", f"{b.penalties['换产成本']:,.0f} 元")
    c8.metric("方案B VPP 补贴", f"{b.dr_subsidy:,.2f} 元")

    if b.dr_stats:
        ok = sum(1 for s in b.dr_stats if s["pass"])
        st.success(f"DR 事件 {len(b.dr_stats)} 个，达标 {ok} 个；"
                   f"窗口 {'、'.join(s['window'] for s in b.dr_stats)}")
    if p.urgent_orders:
        st.info(f"急单：{b.urgent_summary()}")

    with st.expander("成本拆解"):
        st.dataframe(pd.DataFrame({"方案A": a.penalties, "方案B": b.penalties}))

    tabs = st.tabs(["甘特与设备", "节点电价", "电碳与负荷", "储能与库存",
                    "MRP II 报表", "虚拟电厂", "多维评估"])
    with tabs[0]:
        st.pyplot(fig_gantt(a, "方案A 甘特图（6 设备）"))
        st.pyplot(fig_gantt(b, "方案B 甘特图（6 设备）"))
        c1, c2 = st.columns(2)
        c1.pyplot(fig_device_util(b))
        c2.pyplot(fig_device_load(b))
    with tabs[1]:
        st.pyplot(fig_node_heat(b))
        st.caption("同一时段装配区（N2）节点价高于机加区（N1），GA 会把装配工序移出高价区域。")
        st.pyplot(fig_load(b))
    with tabs[2]:
        st.pyplot(fig_energy_carbon(b))
        c1, c2 = st.columns(2)
        c1.pyplot(fig_load(a))
        c2.pyplot(fig_load(b))
    with tabs[3]:
        c1, c2 = st.columns(2)
        c1.pyplot(fig_soc(b))
        c2.pyplot(fig_inv(b))
        st.pyplot(fig_material(b))
    with tabs[4]:
        st.markdown("**生产计划大纲 APP**")
        st.dataframe(pd.DataFrame(b.reports["APP"]), hide_index=True, use_container_width=True)
        st.markdown("**主生产计划 MPS**")
        st.dataframe(pd.DataFrame(b.reports["MPS"]), hide_index=True, use_container_width=True)
        st.markdown("**物料需求计划 MRP**")
        st.dataframe(pd.DataFrame(b.reports["MRP"]), hide_index=True, use_container_width=True)
        st.markdown("**能力需求计划 CRP**")
        c1, c2 = st.columns([3, 2])
        c1.dataframe(pd.DataFrame(b.reports["CRP"]), hide_index=True, use_container_width=True)
        c2.pyplot(fig_crp(b))
    with tabs[5]:
        st.pyplot(fig_dr(b))
        if b.dr_stats:
            st.dataframe(pd.DataFrame(b.dr_stats), hide_index=True, use_container_width=True)
    with tabs[6]:
        c1, c2 = st.columns([2, 3])
        c1.pyplot(fig_radar(a, b))
        c2.markdown(f"""
**自动诊断**

1. 统筹派总成本比敏捷派低 **{a.total_cost - b.total_cost:,.2f}** 元
   （电费 {b.ele_cost:,.2f}、物料 {b.mat_cost:,.0f}、人工 {b.labor_cost:,.0f}、换产 {b.penalties['换产成本']:,.0f}）。
2. 碳排 **{b.carbon:.1f} tCO2**，免费配额 {C.FREE_QUOTA:.0f} t，
   {'仍可获补贴' if b.carbon < C.FREE_QUOTA else '需购买 ' + f'{(b.carbon - C.FREE_QUOTA):.1f} t 配额'}。
3. 设备最高负载率 **{max(v['rate'] for v in b.device_util.values()) * 100:.0f}%**，
   最低 **{min(v['rate'] for v in b.device_util.values()) * 100:.0f}%**，FJSP 把负荷摊到了 6 台设备上。
4. 缺料等待 **{b.shortage_wait * 15}** 分钟，完工 **{b.makespan * 0.25:.2f}** 时。
""")


def show_urgent(p):
    st.header("🚨 不同紧急订单方案对比")
    rows = []
    prog = st.progress(0.0, text="计算中…")
    presets = list(C.urgent_presets().items())
    for i, (name, o) in enumerate(presets):
        r = C.run_ga(C.with_urgent(p, o), pop_size=14, generations=10)
        rows.append({"急单方案": name, "产品": o.product if o else "—",
                     "数量": str(o.qty) if o else "—",
                     "到达(h)": f"{o.arrive_h:.1f}" if o else "—",
                     "交期(h)": f"{o.due_h:.1f}" if o else "—",
                     "总成本": round(r.total_cost, 1), "电费": round(r.ele_cost, 1),
                     "物料": round(r.mat_cost, 0), "换产": r.penalties["换产成本"],
                     "碳排": round(r.carbon, 1), "完工(h)": round(r.makespan * 0.25, 2),
                     "急单完成": (f"{r.urgent_stats[0]['finish'] * 0.25:.2f}" if r.urgent_stats else "—"),
                     "按期": ("是" if r.on_time else "否") if r.urgent_stats else "—"})
        prog.progress((i + 1) / len(presets), text=name)
    prog.empty()
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)
    fig, ax = plt.subplots(figsize=(9, 3.0))
    ax.bar(df["急单方案"], df["总成本"], color="#4C78A8")
    for i, v in enumerate(df["总成本"]):
        ax.text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("总成本(元)")
    plt.setp(ax.get_xticklabels(), rotation=12, ha="right", fontsize=7.5)
    fig.tight_layout()
    st.pyplot(fig)


def show_batch(p):
    st.header("📊 控制变量四场景")
    rows = []
    for name, kw in C.control_cases():
        pp = C.with_urgent(C.Params(**{**p.__dict__, **kw}), None)
        r = C.run_ga(pp, pop_size=12, generations=9)
        rows.append({"场景": name, "总成本": round(r.total_cost, 1), "电费": round(r.ele_cost, 1),
                     "碳排": round(r.carbon, 1), "物料": round(r.mat_cost, 0),
                     "人工": round(r.labor_cost, 0), "缺口等待": r.shortage_wait})
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)
    fig, ax = plt.subplots(figsize=(9, 3.0))
    x = np.arange(len(df))
    ax.bar(x - 0.2, df["总成本"], 0.4, color="#4C78A8", label="总成本")
    ax2 = ax.twinx()
    ax2.bar(x + 0.2, df["碳排"], 0.4, color="#E45756", label="碳排")
    ax.set_xticks(x)
    ax.set_xticklabels(df["场景"], fontsize=7.5)
    ax.set_ylabel("总成本(元)")
    ax2.set_ylabel("tCO2")
    ax.legend(fontsize=7, loc="upper right")
    ax.set_title("硬件（光储）与柔性排产的协同")
    fig.tight_layout()
    st.pyplot(fig)


def show_pareto(p):
    st.header("📈 电费—碳排帕累托前沿")
    xs, ys, cs = [], [], []
    prog = st.progress(0.0, text="扫描中…")
    ws = np.linspace(0.1, 0.9, 9)
    for i, w in enumerate(ws):
        r = C.run_ga(C.replace(C.with_urgent(p, None), w_ele=float(w)),
                     pop_size=10, generations=8, seed=int(w * 100))
        xs.append(r.ele_cost)
        ys.append(r.carbon)
        cs.append(r.total_cost)
        prog.progress((i + 1) / len(ws))
    prog.empty()
    fig, ax = plt.subplots(figsize=(6.8, 3.8))
    sc = ax.scatter(xs, ys, c=cs, cmap="viridis", s=80, edgecolor="k")
    order = np.argsort(xs)
    ax.plot(np.array(xs)[order], np.array(ys)[order], "r--", alpha=0.6)
    ax.set_xlabel("电费(元)")
    ax.set_ylabel("碳排(tCO2)")
    ax.set_title("经济性—碳排放权衡前沿")
    fig.colorbar(sc, label="总成本(元)")
    fig.tight_layout()
    st.pyplot(fig)


def show_game(p):
    st.header("🎮 碳价博弈推演")
    prices = [20, 40, 60, 80, 100, 120]
    rows, costs, carbons = [], [], []
    prog = st.progress(0.0, text="扫描中…")
    for i, cp in enumerate(prices):
        r = C.run_ga(C.replace(C.with_urgent(p, None), carbon_price=float(cp)),
                     pop_size=10, generations=8, seed=cp)
        rows.append({"碳价": cp, "总成本": round(r.total_cost, 1), "碳排": round(r.carbon, 1),
                     "电费": round(r.ele_cost, 1),
                     "碳交易": r.penalties["碳交易(负为补贴)"]})
        costs.append(r.total_cost)
        carbons.append(r.carbon)
        prog.progress((i + 1) / len(prices))
    prog.empty()
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    f1, ax1 = plt.subplots(figsize=(5.6, 2.8))
    ax1.plot(prices, costs, "o-", color="purple")
    ax1.set_xlabel("碳价(元/t)")
    ax1.set_ylabel("总成本(元)")
    ax1.grid(alpha=0.3)
    c1.pyplot(f1)
    f2, ax2 = plt.subplots(figsize=(5.6, 2.8))
    ax2.plot(prices, carbons, "s-", color="green")
    ax2.set_xlabel("碳价(元/t)")
    ax2.set_ylabel("碳排(tCO2)")
    ax2.grid(alpha=0.3)
    c2.pyplot(f2)


# --------------------------------------------------------------------- 主流程
_CUR_P, btns = sidebar()

if btns["single"]:
    with st.spinner("双方案求解中（6 设备 FJSP + GA）…"):
        st.session_state["a"] = C.run_greedy(_CUR_P)
        st.session_state["b"] = C.run_ga(_CUR_P, pop_size=18, generations=14)
    st.session_state["p"] = _CUR_P
    st.session_state["mode"] = "single"
elif btns["urgent"]:
    st.session_state["mode"] = "urgent"
elif btns["batch"]:
    st.session_state["mode"] = "batch"
elif btns["pareto"]:
    st.session_state["mode"] = "pareto"
elif btns["game"]:
    st.session_state["mode"] = "game"

mode = st.session_state.get("mode")

if mode == "single":
    show_single(st.session_state["p"], st.session_state["a"], st.session_state["b"])
elif mode == "urgent":
    show_urgent(_CUR_P)
elif mode == "batch":
    show_batch(_CUR_P)
elif mode == "pareto":
    show_pareto(_CUR_P)
elif mode == "game":
    show_game(_CUR_P)
else:
    st.info("👈 左侧配置参数后，点击任意运行按钮。")
    st.markdown("""
**V3 升级点**

| 维度 | 内容 |
| --- | --- |
| 生产结构 | 5 种物料（含外购电机、辅料）× 4 半成品 × 3 成品，网状 BOM |
| 设备 | 6 台，新老代差（老设备慢但省电），同工序多台可选（FJSP） |
| 换型 | 按材质矩阵（钢/铝/外购/混合）取换产步数与成本 |
| 成本 | 电费 + 物料 + 人工加班 + 换产 + 返工 + 需量 + 交期 + 碳交易 − VPP 补贴 |
| 电价 | 节点电价（N1/N2/N3）+ 阻塞价差 + 网损，热力图呈现 |
| 碳 | 分时碳因子（夜间高、午间低），电碳双轴对比 |
| VPP | 随机 DR 事件、申报基线 vs 实际负荷、压降补贴 |
| 报表 | APP / MPS / MRP / CRP 闭环 MRP II |
""")

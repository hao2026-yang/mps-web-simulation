# -*- coding: utf-8 -*-
"""零碳园区 MPS 工序级排产多方案推演系统（扩展版）

运行：streamlit run agent_new_pro.py
依赖：streamlit, numpy, pandas, matplotlib（本文件同目录需有 mps_core.py）

相对原 agent_new.py 新增
  · 生产结构：P1/P2/P3 半成品 + FA/FB/FC 成品，三级 BOM 与原材料 M1/M2/M3
  · 原材料提前期：期初库存 + 到货批次，缺料工序自动顺延并计入等待
  · 节点电价：机加区 / 装配区 / 园区关口表三节点，含阻塞价差与网损
  · 紧急订单：产品/数量/到达/交期/罚金可配置，可一键对比 5 种急单方案
  · 调度解码修正：前置与齐套不满足时“排队等待”，不再静默丢弃任务
"""

import os
import sys

import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mps_core as C  # noqa: E402

st.set_page_config(page_title="MPS 多算法排产仿真（扩展版）", layout="wide")
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

COLORS = {"P1": "#4C78A8", "P2": "#F58518", "P3": "#54A24B",
          "FA": "#2F4B7C", "FB": "#E45756", "FC": "#72B7B2"}
NODE_COLOR = {"N1": "#4C78A8", "N2": "#E45756", "N3": "#54A24B"}

st.title("零碳园区 MPS 工序级排产多方案推演系统 · 扩展版")
st.caption("三级 BOM 与原材料提前期 ｜ 节点电价 ｜ 可配置紧急订单 ｜ 双引擎对比")


# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------
def sidebar_params() -> C.Params:
    with st.sidebar:
        st.header("📦 订单与生产结构")
        p1 = st.number_input("P1 半成品计划", 0, 60, 10, 1)
        p2 = st.number_input("P2 半成品计划", 0, 60, 8, 1)
        p3 = st.number_input("P3 半成品计划（新增）", 0, 60, 6, 1)
        fa = st.number_input("FA 成品计划", 0, 40, 4, 1)
        fb = st.number_input("FB 成品计划", 0, 40, 3, 1)
        fc = st.number_input("FC 成品计划（新增）", 0, 40, 2, 1)
        with st.expander("BOM 与工艺路线", expanded=False):
            st.dataframe(pd.DataFrame(C.bom_table()), hide_index=True)

        st.header("🚚 原材料与提前期")
        lead_scale = st.slider("采购提前期系数", 0.5, 2.0, 1.0, 0.1,
                               help="1.0 = M1 6h / M2 8h / M3 12h；越小到货越早")
        init_scale = st.slider("期初库存系数", 0.5, 2.0, 1.0, 0.1)
        with st.expander("物料主数据", expanded=False):
            st.dataframe(pd.DataFrame(C.material_table()), hide_index=True)

        st.header("🔌 节点电价")
        congestion = st.slider("节点阻塞价差系数", 0.0, 2.0, 1.0, 0.1,
                               help="0 = 无节点差异；越大则装配区高峰电价越高")
        st.caption("关口价 0.30/0.90/0.50 元·kWh⁻¹（谷/峰/平），叠加节点价差与网损")

        st.header("🚨 紧急订单")
        presets = C.urgent_presets()
        preset_name = st.selectbox("急单方案", list(presets.keys()), index=0)
        urgent = presets[preset_name]
        use_custom = st.checkbox("自定义急单", value=False)
        if use_custom:
            uc1, uc2 = st.columns(2)
            u_prod = uc1.selectbox("产品", list(C.FINALS), index=0)
            u_qty = uc2.number_input("数量", 1, 10, 2, 1)
            u_arr = st.slider("到达时刻(h)", 0.0, 23.0, 12.0, 0.5)
            u_due = st.slider("要求交付(h)", 1.0, 24.0, 18.0, 0.5)
            u_pen = st.number_input("误期罚金(元/步)", 0, 500, 60, 10)
            urgent = C.UrgentOrder(u_prod, int(u_qty), float(u_arr), float(u_due),
                                   float(u_pen), f"自定义{u_prod}×{int(u_qty)}")

        st.header("👷 排班与生产质量")
        day_wage = st.number_input("白班时薪(元)", 0, 200, 22, 1)
        night_wage = st.number_input("夜班时薪(元)", 0, 200, 34, 1)
        base_defect = st.slider("白班基础次品率", 0.0, 0.10, 0.02, 0.01)
        night_inc = st.slider("夜班次品率增量", 0.0, 0.05, 0.02, 0.01)
        rework_cost = st.number_input("单件返工成本(元)", 0, 200, 16, 1)

        st.header("🎲 随机扰动与物理约束")
        fault_prob = st.slider("设备每步故障概率", 0.0, 0.10, 0.02, 0.01)
        ramp_limit = st.slider("功率爬坡限值(kW/步)", 10, 100, 25, 5)
        seed = st.number_input("随机种子", 0, 9999, 42, 1)

        st.header("⚙️ 优化权重")
        w_ele = st.slider("电费权重 w_ele", 0.0, 1.0, 0.6, 0.1)
        st.info(f"碳排放权重 w_carbon = {round(1 - w_ele, 2)}")

        st.header("🧪 控制变量开关")
        use_pv = st.checkbox("启用光伏", True)
        use_storage = st.checkbox("启用储能", True)
        use_flex = st.checkbox("允许工序柔性调度", True)

        st.header("🏭 市场参数")
        carbon_price = st.slider("碳价(元/吨)", 20, 200, 60, 10)
        grid_mult = st.slider("电网电价倍数", 1.0, 2.0, 1.0, 0.1)

        st.markdown("---")
        b_single = st.button("▶ 运行当前场景（双方案）")
        b_urgent = st.button("🚨 对比 5 种急单方案")
        b_batch = st.button("📊 四场景控制变量实验")
        b_pareto = st.button("📈 帕累托前沿")
        b_game = st.button("🎮 碳价博弈推演")

    p = C.Params(
        plans={"P1": int(p1), "P2": int(p2), "P3": int(p3),
               "FA": int(fa), "FB": int(fb), "FC": int(fc)},
        lead_scale=lead_scale, init_scale=init_scale, congestion=congestion,
        day_wage=day_wage, night_wage=night_wage, base_defect=base_defect,
        night_defect_inc=night_inc, rework_cost=rework_cost,
        fault_prob=fault_prob, ramp_limit=ramp_limit, w_ele=w_ele,
        use_pv=use_pv, use_storage=use_storage, use_flex=use_flex,
        carbon_price=float(carbon_price), grid_mult=grid_mult, seed=int(seed),
        urgent_orders=(urgent,) if urgent else (),
    )
    return p, {"single": b_single, "urgent": b_urgent, "batch": b_batch,
               "pareto": b_pareto, "game": b_game}


# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------
def fig_gantt(res: C.Result, title: str):
    fig, ax = plt.subplots(figsize=(9, 3.4))
    rows = {"A": 1, "B": 2, "C": 3}
    for item in res.schedule:
        urgent = item["Urgent"]
        ax.barh(rows[item["Device"]], item["Duration"], left=item["Start"],
                color=COLORS.get(item["Product"], "#888"),
                edgecolor="red" if urgent else "#333",
                linewidth=2.2 if urgent else 0.6, alpha=0.9 if urgent else 0.8)
    for s in res.urgent_stats:
        ax.axvline(s["arrive"], color="red", linestyle="--", linewidth=1.6, alpha=0.8)
        ax.annotate("急单到达", (s["arrive"], 3.55), color="red", fontsize=9,
                    ha="left", va="bottom")
        ax.axvline(s["due"], color="orange", linestyle=":", linewidth=1.2, alpha=0.9)
    ax.axvline(C.DUE_STEP, color="#555", linestyle="-.", linewidth=1.0, alpha=0.7)
    ax.annotate("22:00 交付", (C.DUE_STEP - 1, 0.45), color="#555", fontsize=8, ha="right")
    ax.set_yticks([1, 2, 3]); ax.set_yticklabels(["设备A", "设备B", "设备C"])
    ax.set_xlim(0, C.T); ax.set_ylim(0.4, 3.9)
    ax.set_xlabel("时段（15 min/格）"); ax.set_title(title)
    ax2 = ax.twinx()
    top = max(1.0, float(res.base_price.max()) * 1.15)
    ax2.plot(res.base_price, color="#d62728", alpha=0.35, linestyle="--", linewidth=1)
    pv_top = float(res.pv.max()) or 1.0
    ax2.fill_between(range(C.T), 0, res.pv / pv_top * top * 0.55,
                     color="green", alpha=0.13)
    ax2.set_ylim(0, top); ax2.set_yticks([])
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[k]) for k in C.PRODUCTS]
    ax.legend(handles, list(C.PRODUCTS), ncol=6, fontsize=7, framealpha=0.6,
              loc="upper center", bbox_to_anchor=(0.5, -0.22))
    fig.tight_layout()
    return fig


def fig_node_price(res: C.Result):
    fig, ax = plt.subplots(figsize=(9, 2.6))
    ax.plot(res.base_price, color="#333", linestyle="--", linewidth=1.2, label="园区关口价")
    for n in C.NODES:
        ax.plot(res.prices[n], color=NODE_COLOR[n], linewidth=1.4, label=f"{n} {C.NODES[n]}")
    ax.axvspan(28, 68, color="orange", alpha=0.06)
    ax.set_xlim(0, C.T); ax.set_ylabel("元/kWh"); ax.legend(ncol=4, fontsize=8)
    ax.set_title("节点电价曲线（10-17 点高峰段节点价差最大）")
    fig.tight_layout()
    return fig


def fig_load(res: C.Result, title: str):
    fig, ax = plt.subplots(figsize=(10, 2.4))
    stack_nodes = [n for n in ("N1", "N2")]
    ax.stackplot(range(C.T), [res.node_load[n] for n in stack_nodes],
                 labels=[f"{n} {C.NODES[n]}" for n in stack_nodes],
                 colors=[NODE_COLOR[n] for n in stack_nodes], alpha=0.55)
    ax.plot(res.grid_import, color="#111", linewidth=1.2, label="购电负荷")
    ax.fill_between(range(C.T), 0, res.pv, color="green", alpha=0.2, label="光伏出力")
    ax.axhline(C.PEAK_LIMIT, color="red", linestyle="-.", linewidth=1, label="需量阈值 80kW")
    ax.set_xlim(0, C.T); ax.set_ylabel("kW"); ax.legend(fontsize=7, ncol=5, loc="upper right")
    ax.set_title(title)
    fig.tight_layout()
    return fig


def fig_soc(res: C.Result):
    fig, ax = plt.subplots(figsize=(6, 2.2))
    ax.plot(res.soc, color="orange", label="SOC")
    ax.set_ylim(0, 1); ax.set_title("储能 SOC（谷充峰放）"); ax.set_xlim(0, C.T)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


def fig_inventory(res: C.Result):
    fig, ax = plt.subplots(figsize=(6, 2.2))
    for s in C.SEMI:
        ax.plot(res.inv[s], drawstyle="steps-post", label=f"{s} 库存", color=COLORS[s])
    ax.set_title("半成品库存（阶梯反映齐套消耗）"); ax.set_xlim(0, C.T); ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


def fig_material(res: C.Result):
    fig, ax = plt.subplots(figsize=(9, 2.2))
    colors = {"M1": "#8C6D31", "M2": "#4C78A8", "M3": "#B07AA1"}
    for m in C.MATERIALS:
        ax.plot(res.mat_stock[m], drawstyle="steps-post", label=m, color=colors[m])
    ax.set_title("原材料库存（台阶处为到货/领料，缺料时工序顺延）")
    ax.set_xlim(0, C.T); ax.set_ylabel("件"); ax.legend(fontsize=8, ncol=3)
    fig.tight_layout()
    return fig


def fig_radar(a: C.Result, b: C.Result):
    def norm(res, other):
        cost = 100 * min(1.0, other.total_cost / max(res.total_cost, 1e-6))
        ontime = 100 * (1.0 - min(1.0, res.delay_cost / max(0.15 * res.total_cost, 1.0)))
        quality = 100 * (1.0 - res.night_share)
        green = 100 * min(1.0, res.pv_selfuse)
        flex = 100 * min(1.0, res.flex_save / 20.0) if res.flex_save > 0 else 45.0
        return [flex, green, ontime, quality, cost]

    labels = ["时序柔性\n避峰", "能源柔性\n消纳光伏", "交付保障", "质量风险\n避夜班", "经济性"]
    va, vb = norm(a, b), norm(b, a)
    ang = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    ang += ang[:1]; va += va[:1]; vb += vb[:1]
    fig, ax = plt.subplots(figsize=(5.4, 5.0), subplot_kw=dict(polar=True))
    ax.plot(ang, va, "o-", color="#1f77b4", label="方案A 敏捷派")
    ax.fill(ang, va, color="#1f77b4", alpha=0.15)
    ax.plot(ang, vb, "o-", color="#2ca02c", label="方案B 统筹派")
    ax.fill(ang, vb, color="#2ca02c", alpha=0.15)
    ax.set_thetagrids(np.degrees(ang[:-1]), labels, fontsize=9)
    ax.set_ylim(0, 100); ax.set_title("多维柔性评估", y=1.08)
    ax.legend(loc="upper right", bbox_to_anchor=(1.32, 1.12), fontsize=8)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# 结果展示
# ---------------------------------------------------------------------------
def show_compare(p: C.Params, a: C.Result, b: C.Result):
    st.subheader("🏆 核心指标")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("方案A 敏捷派 总成本", f"{a.total_cost:.2f} 元")
    c2.metric("方案B 统筹派 总成本", f"{b.total_cost:.2f} 元",
              delta=f"{b.total_cost - a.total_cost:.2f}", delta_color="inverse")
    c3.metric("方案A 碳排", f"{a.carbon:.2f} tCO2")
    c4.metric("方案B 碳排", f"{b.carbon:.2f} tCO2",
              delta=f"{b.carbon - a.carbon:.2f}", delta_color="inverse")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("方案B 物料成本", f"{b.mat_cost:.2f} 元")
    c6.metric("方案B 换产成本", f"{b.penalties['换产成本']:.2f} 元")
    c7.metric("方案B 缺料等待", f"{b.shortage_wait * 15} 分钟")
    c8.metric("方案B 完工 / 延误工序", f"{b.makespan * 0.25:.2f} 时 / {b.late_tasks} 道")

    if p.urgent_orders:
        st.success(f"急单状态：{b.urgent_summary()}")
    else:
        st.info("当前未插急单；可在左侧选择急单方案后重新运行。")

    st.info(f"""
**自动诊断**
1. **经济性**：统筹派总成本比敏捷派 {'低' if b.total_cost < a.total_cost else '高'} **{abs(a.total_cost - b.total_cost):.2f}** 元
   （其中电费 {b.ele_cost:.2f} 元、物料 {b.mat_cost:.2f} 元、换产 {b.penalties['换产成本']:.2f} 元、交期罚 {b.penalties['交期违约']:.2f} 元）。
2. **齐套与备料**：方案B 因物料未到或半成品不足共等待 **{b.shortage_wait * 15}** 分钟，
   缺料惩罚 {b.penalties['缺料惩罚']:.2f} 元；提前期越短、期初库存越足，等待越少。
3. **节点电价**：高峰段装配区节点价比机加区高
   **{(b.prices['N2'][40] - b.prices['N1'][40]):.3f}** 元/kWh，因此把装配工序挪出高峰更能省钱。
4. **碳与绿电**：方案B 光伏自消纳率 **{b.pv_selfuse * 100:.1f}%**，碳排 **{b.carbon:.2f}** tCO2
   （免费配额 {C.FREE_QUOTA:.0f} t，{ '尚需购买' if b.carbon > C.FREE_QUOTA else '仍有余量可获补贴' }）。
""")

    with st.expander("查看两方案成本拆解"):
        df = pd.DataFrame({"方案A": a.penalties, "方案B": b.penalties})
        st.dataframe(df)

    st.markdown("---")
    c1, c2 = st.columns(2)
    c1.pyplot(fig_gantt(a, "方案A 甘特图（敏捷派）"))
    c2.pyplot(fig_gantt(b, "方案B 甘特图（统筹派）"))

    st.markdown("---")
    st.subheader("⚡ 节点电价与用电负荷")
    st.pyplot(fig_node_price(b))
    c1, c2 = st.columns(2)
    c1.pyplot(fig_load(a, "方案A 负荷"))
    c2.pyplot(fig_load(b, "方案B 负荷"))

    st.markdown("---")
    st.subheader("🔋 储能与半成品齐套")
    c1, c2 = st.columns(2)
    c1.pyplot(fig_soc(b))
    c2.pyplot(fig_inventory(b))

    st.markdown("---")
    st.subheader("🚚 原材料库存时序（方案B）")
    st.pyplot(fig_material(b))

    st.markdown("---")
    st.subheader("🕸️ 多维柔性综合评估")
    st.pyplot(fig_radar(a, b))



def show_urgent_compare(p: C.Params):
    st.header("🚨 不同紧急订单方案的排产结果对比")
    st.caption("同一基准计划（不含急单），逐一插入不同急单，各自重跑遗传算法。注意总成本、"
               "完工时刻与按期状态都会随急单的产品、数量、到达与交期而变化。")
    rows = []
    prog = st.progress(0.0, text="方案对比中…")
    presets = list(C.urgent_presets().items())
    for i, (name, order) in enumerate(presets):
        pp = C.with_urgent(p, order)
        res = C.run_ga(pp, pop_size=16, generations=14)
        row = {
            "急单方案": name,
            "产品": order.product if order else "—",
            "数量": str(order.qty) if order else "—",
            "到达(h)": f"{order.arrive_h:.1f}" if order else "—",
            "交期(h)": f"{order.due_h:.1f}" if order else "—",
            "总成本(元)": round(res.total_cost, 2),
            "电费(元)": round(res.ele_cost, 2),
            "换产(元)": res.penalties["换产成本"],
            "交期罚(元)": res.penalties["交期违约"],
            "碳排(tCO2)": round(res.carbon, 2),
            "完工(h)": round(res.makespan * 0.25, 2),
            "急单完成(h)": f"{res.urgent_stats[0]['finish'] * 0.25:.2f}" if res.urgent_stats else "—",
            "是否按期": ("是" if res.on_time else "否") if res.urgent_stats else "—",
        }
        rows.append(row)
        prog.progress((i + 1) / len(presets), text=f"已完成：{name}")
    prog.empty()

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True)

    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.bar(df["急单方案"], df["总成本(元)"], color="#4C78A8")
    for i, v in enumerate(df["总成本(元)"]):
        ax.text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("总成本(元)"); ax.set_title("不同急单方案的总成本")
    plt.setp(ax.get_xticklabels(), rotation=15, ha="right", fontsize=8)
    fig.tight_layout()
    st.pyplot(fig)

    base = df.iloc[0]["总成本(元)"]
    worst = df.iloc[1:].sort_values("总成本(元)").iloc[-1]
    st.info(f"不插急单时总成本 {base:.2f} 元；代价最大的是「{worst['急单方案']}」，"
            f"总成本 {worst['总成本(元)']:.2f} 元（+{worst['总成本(元)'] - base:.2f} 元）。"
            "差异来自急单产品路线占用设备、到达时刻挤压排产窗口、以及为保交付而增加的换产。")


def show_batch(p: C.Params):
    st.header("📊 控制变量法四场景对照")
    rows = []
    prog = st.progress(0.0, text="场景计算中…")
    cases = C.control_cases()
    for i, (name, kw) in enumerate(cases):
        pp = C.with_urgent(C.Params(**{**p.__dict__, **kw}), None)
        res = C.run_ga(pp, pop_size=14, generations=12)
        rows.append({"场景": name, "总成本(元)": round(res.total_cost, 2),
                     "电费(元)": round(res.ele_cost, 2),
                     "碳排(tCO2)": round(res.carbon, 2),
                     "物料(元)": round(res.mat_cost, 2),
                     "缺口等待(步)": res.shortage_wait})
        prog.progress((i + 1) / len(cases), text=f"已完成：{name}")
    prog.empty()
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True)
    fig, ax1 = plt.subplots(figsize=(9, 3.2))
    ax2 = ax1.twinx()
    x = np.arange(len(df))
    ax1.bar(x - 0.2, df["总成本(元)"], 0.4, color="#4C78A8", label="总成本")
    ax2.bar(x + 0.2, df["碳排(tCO2)"], 0.4, color="#E45756", label="碳排")
    ax1.set_xticks(x); ax1.set_xticklabels(df["场景"], fontsize=8)
    ax1.set_ylabel("总成本(元)"); ax2.set_ylabel("tCO2")
    ax1.set_title("四场景：硬件（光储）与柔性排产的协同效应")
    fig.tight_layout(); st.pyplot(fig)


def show_pareto(p: C.Params):
    st.header("📈 电费—碳排帕累托前沿")
    xs, ys, cs = [], [], []
    prog = st.progress(0.0, text="权重扫描中…")
    weights = np.linspace(0.1, 0.9, 9)
    for i, w in enumerate(weights):
        pp = C.with_urgent(C.replace(p, w_ele=float(w)), None)
        res = C.run_ga(pp, pop_size=12, generations=10, seed=int(w * 100))
        xs.append(res.ele_cost); ys.append(res.carbon); cs.append(res.total_cost)
        prog.progress((i + 1) / len(weights), text=f"w_ele={w:.1f}")
    prog.empty()
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    sc = ax.scatter(xs, ys, c=cs, cmap="viridis", s=90, edgecolor="k")
    order = np.argsort(xs)
    ax.plot(np.array(xs)[order], np.array(ys)[order], "r--", alpha=0.6)
    ax.set_xlabel("电费(元)"); ax.set_ylabel("碳排(tCO2)")
    ax.set_title("经济性—碳排放权衡前沿")
    fig.colorbar(sc, label="总成本(元)")
    fig.tight_layout(); st.pyplot(fig)


def show_game(p: C.Params):
    st.header("🎮 碳价博弈推演")
    prices = [20, 40, 60, 80, 100, 120]
    costs, carbons, rows = [], [], []
    prog = st.progress(0.0, text="碳价扫描中…")
    for i, cp in enumerate(prices):
        pp = C.with_urgent(C.replace(p, carbon_price=float(cp)), None)
        res = C.run_ga(pp, pop_size=12, generations=10, seed=cp)
        costs.append(res.total_cost); carbons.append(res.carbon)
        rows.append({"碳价": cp, "总成本(元)": round(res.total_cost, 2),
                     "碳排(tCO2)": round(res.carbon, 2),
                     "电费(元)": round(res.ele_cost, 2),
                     "碳交易(元)": res.penalties["碳交易(负为补贴)"]})
        prog.progress((i + 1) / len(prices), text=f"碳价 {cp} 元/吨")
    prog.empty()
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    c1, c2 = st.columns(2)
    f1, ax1 = plt.subplots(figsize=(6, 3.2))
    ax1.plot(prices, costs, "o-", color="purple"); ax1.set_xlabel("碳价(元/吨)")
    ax1.set_ylabel("总成本(元)"); ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.set_title("碳价 → 总成本")
    c1.pyplot(f1)
    f2, ax2 = plt.subplots(figsize=(6, 3.2))
    ax2.plot(prices, carbons, "s-", color="green"); ax2.set_xlabel("碳价(元/吨)")
    ax2.set_ylabel("碳排(tCO2)"); ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.set_title("碳价 → 碳排放")
    c2.pyplot(f2)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
params, btns = sidebar_params()

if btns["single"]:
    with st.spinner("双方案求解中（贪婪 + 遗传算法）…"):
        res_a = C.run_greedy(params)
        res_b = C.run_ga(params, pop_size=20, generations=18)
    st.session_state["mode"] = "single"
    st.session_state["a"], st.session_state["b"] = res_a, res_b
    st.session_state["p"] = params

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
    show_compare(st.session_state["p"], st.session_state["a"], st.session_state["b"])
elif mode == "urgent":
    show_urgent_compare(params)
elif mode == "batch":
    show_batch(params)
elif mode == "pareto":
    show_pareto(params)
elif mode == "game":
    show_game(params)
else:
    st.info("👈 在左侧配置参数后，点击任意运行按钮。")
    st.markdown("""
**本版新增能力**

| 能力 | 说明 |
| --- | --- |
| 生产结构 | 3 种半成品（P1/P2/P3）+ 3 种成品（FA/FB/FC），各自 BOM 与工艺路线 |
| 原材料与提前期 | M1 钢材 / M2 铝材 / M3 标准件，期初库存 + 到货批次，缺料自动顺延 |
| 节点电价 | 机加区、装配区、园区关口表三节点，含阻塞价差与网损分摊 |
| 紧急订单 | 产品/数量/到达/交期/罚金可配置，支持 5 种方案一键对比 |
| 调度修正 | 前置或齐套不满足时排队等待，不再丢弃任务，结果可复现 |
""")

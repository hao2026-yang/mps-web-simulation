# 杨博皓 信管2403 220241060922
# ERP课程设计【完整复刻word文档全部参数+新增控制变量实验｜修复语法bug】
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

st.set_page_config(page_title="工序级生产‑电力碳协同仿真", layout="wide", initial_sidebar_state="expanded")

TIME_STEP = 0.25
TOTAL_PERIOD = 96
time_array = np.arange(0, TOTAL_PERIOD * TIME_STEP, TIME_STEP)
start_base_datetime = datetime(2026, 1, 1, 0, 0, 0)

# ===================== 侧边栏【完整复刻word截图全部参数，一个不漏】 =====================
with st.sidebar:
    st.header("🔧 仿真基础参数")
    st.subheader("⚡电力市场惩罚&柔性约束")
    power_deviation_penalty = st.number_input("功率偏差惩罚元/kW", value=0.30, min_value=0.0)
    flex_interrupt_cost = st.number_input("柔性工序单次中断成本元", value=120, min_value=0)
    flex_min_response_kW = st.number_input("柔性负荷最小响应容量kW", value=2.00, min_value=0.0)
    device_ramp_limit = st.number_input("设备功率爬坡限值(kW/15min步)", value=25.00, min_value=0.0)

    st.subheader("🏭碳市场参数")
    carbon_quota_free = st.number_input("园区免费碳配额 tCO₂", value=90, min_value=0)
    carbon_price_base = st.number_input("基准碳价元/tCO₂", value=60, min_value=0)

    st.subheader("📋MPS订单&工艺约束")
    order_p1 = st.number_input("P1半成品计划件数", min_value=1, max_value=100, value=25)
    order_p2 = st.number_input("P2半成品计划件数", min_value=1, max_value=100, value=20)

    st.subheader("👷工人排班&生产质量｜随机扰动")
    day_wage = st.number_input("白班时薪元", value=22)
    night_wage = st.number_input("夜班时薪元", value=34)
    day_base_defect = st.slider("白班基础次品率", min_value=0.0, max_value=0.1, value=0.02, step=0.001)
    night_drift = st.slider("夜班每时段次品率增量", min_value=0.0, max_value=0.05, value=0.00, step=0.001)
    rework_cost_unit = st.number_input("单件返工成本元", value=16)
    device_fault_prob = st.slider("设备每步故障概率", min_value=0.0, max_value=0.1, value=0.02, step=0.001)
    op_rework_prob = st.slider("工序返工概率", min_value=0.0, max_value=0.1, value=0.02, step=0.001)

    st.subheader("⚖优化权重")
    w_ele = st.slider("电费权重 w_ele", min_value=0.0, max_value=1.0, value=0.60, step=0.01)
    w_carbon = 1.0 - w_ele
    st.info(f"碳排放权重 w_carbon = {w_carbon:.2f}")

    st.divider()
    st.subheader("🧪 学姐建议｜控制变量实验开关")
    enable_pv = st.checkbox("启用光伏", value=True)
    enable_ess = st.checkbox("启用储能", value=True)
    enable_flex = st.checkbox("允许工序柔性调度", value=True)

    run_single = st.button("▶ 运行当前场景仿真")
    run_batch = st.button("📊 批量跑4组对照场景（控制变量）")

# 电价序列
price_dayahead = np.array([0.42,0.44,0.48,0.55,0.62,0.7,0.76,0.82,0.78,0.7,0.6,0.52,0.45,0.4,0.38,0.41]*6)
price_intraday = price_dayahead * 1.04
price_real = price_dayahead * 1.08

# 工艺路线（全部改为英文短横杠）
process_route = {
    "P1": [
        {"op_id": "P1-O1", "device": "A", "duration": 1, "power": 20, "carbon_base": 8},
        {"op_id": "P1-O2", "device": "B", "duration": 0.75, "power": 15, "carbon_base": 6},
        {"op_id": "P1-O3", "device": "C", "duration": 1.25, "power": 18, "carbon_base": 7},
    ],
    "P2": [
        {"op_id": "P2-O1", "device": "A", "duration": 0.8, "power": 16, "carbon_base": 5},
        {"op_id": "P2-O2", "device": "B", "duration": 1, "power": 22, "carbon_base": 9},
    ]
}
device_info = {
    "A": {"min_run": 0.5, "max_ramp": device_ramp_limit, "max_power":30},
    "B": {"min_run": 0.5, "max_ramp": device_ramp_limit, "max_power":28},
    "C": {"min_run": 0.5, "max_ramp": device_ramp_limit, "max_power":25},
}

def run_simulation(enable_pv:bool, enable_ess:bool, enable_flex:bool,
                  ord_p1=order_p1, ord_p2=order_p2,
                  dev_fault_p=device_fault_prob, op_rwk_p=op_rework_prob,
                  c_quota_free=carbon_quota_free, c_price=carbon_price_base,
                  w_e=w_ele, w_c=w_carbon,
                  p_dev_penalty=power_deviation_penalty, flex_min_kW=flex_min_response_kW, flex_int_cost=flex_interrupt_cost,
                  dw=day_wage, nw=night_wage, d_def=day_base_defect, n_dr=night_drift, rw_cost=rework_cost_unit):

    load_forecast = np.zeros(TOTAL_PERIOD)
    load_real = np.zeros(TOTAL_PERIOD)
    pv_power = np.zeros(TOTAL_PERIOD)
    ess_power = np.zeros(TOTAL_PERIOD)
    inventory_p1 = np.zeros(TOTAL_PERIOD)
    inventory_p2 = np.zeros(TOTAL_PERIOD)
    gantt_records = []
    op_power_record = {}
    for dev in device_info:
        op_power_record[dev] = np.zeros(TOTAL_PERIOD)

    fault_cnt = 0
    rework_cnt = 0
    night_hour_sum = 0

    # 光伏出力
    if enable_pv:
        for t in range(TOTAL_PERIOD):
            hour = t * TIME_STEP
            if 6 <= hour <=18:
                pv_power[t] = 40 * np.sin((hour -6)/12 * np.pi)
            else:
                pv_power[t]=0

    product_list = []
    for i in range(ord_p1):
        product_list.append("P1")
    for i in range(ord_p2):
        product_list.append("P2")

    dev_available_time = {"A":0, "B":0, "C":0}
    dev_last_power = {"A":0, "B":0, "C":0}
    inv_p1 = 0
    inv_p2 = 0
    t_idx = 0

    for prod in product_list:
        ops = process_route[prod]
        start_t = max(t_idx * TIME_STEP, dev_available_time[ops[0]["device"]])
        for op in ops:
            dev = op["device"]
            dur = op["duration"]
            p_nom = op["power"]

            # 柔性调度逻辑
            if enable_flex:
                start_t = max(start_t, dev_available_time[dev])
                t_start_idx = int(start_t / TIME_STEP)
                if t_start_idx < TOTAL_PERIOD and price_dayahead[t_start_idx]>0.65:
                    start_t += 1
                    t_start_idx = int(start_t / TIME_STEP)
            else:
                start_t = max(start_t, dev_available_time[dev])
                t_start_idx = int(start_t / TIME_STEP)

            t_end_idx = min(int((start_t + dur)/TIME_STEP), TOTAL_PERIOD)

            # 随机故障、返工
            for ti in range(t_start_idx, t_end_idx):
                h = ti * TIME_STEP
                if h<8 or h>18:
                    night_hour_sum += TIME_STEP
                if np.random.rand() < dev_fault_p:
                    fault_cnt +=1
                if np.random.rand() < op_rwk_p:
                    rework_cnt +=1
                delta_p = p_nom - dev_last_power[dev]
                if abs(delta_p)> device_info[dev]["max_ramp"]:
                    p_act = dev_last_power[dev] + np.sign(delta_p)*device_info[dev]["max_ramp"]
                else:
                    p_act = p_nom
                op_power_record[dev][ti] = p_act
                dev_last_power[dev] = p_act

            start_dt = start_base_datetime + timedelta(hours=start_t)
            end_dt = start_base_datetime + timedelta(hours=start_t + dur)
            gantt_records.append({
                "Task": op["op_id"],
                "Start": start_dt,
                "Finish": end_dt,
                "Resource": dev
            })
            dev_available_time[dev] = start_t + dur
            start_t = start_t + dur
        if prod == "P1":
            inv_p1 +=1
        else:
            inv_p2 +=1
        inventory_p1[t_idx:] = inv_p1
        inventory_p2[t_idx:] = inv_p2
        t_idx += int(dur / TIME_STEP)+1

    for dev in device_info:
        load_forecast += op_power_record[dev]
    load_real = load_forecast.copy()

    # 储能
    soc = 0
    ess_cap = 80
    ess_pmax =20
    if enable_ess:
        for ti in range(TOTAL_PERIOD):
            if price_real[ti]<0.4 and soc < ess_cap*0.9:
                ess_power[ti] = -ess_pmax
                soc += ess_pmax * TIME_STEP
            elif price_real[ti]>0.68 and soc>ess_cap*0.1:
                ess_power[ti] = ess_pmax
                soc -= ess_pmax * TIME_STEP
            else:
                ess_power[ti]=0
            load_real[ti] = load_real[ti] - pv_power[ti] + ess_power[ti]

    # 电力偏差考核
    dev_power_diff = np.abs(load_real - load_forecast)
    deviation_cost = np.sum(dev_power_diff * p_dev_penalty * TIME_STEP)

    elec_cost_dayahead = np.sum(load_forecast * price_dayahead * TIME_STEP)
    elec_cost_real = np.sum(np.maximum(load_real,0)* price_real * TIME_STEP)

    carbon_total = 0
    for dev in op_power_record:
        carbon_total += np.sum(op_power_record[dev] * TIME_STEP) * 0.4
    carbon_balance = c_quota_free - carbon_total
    carbon_income = carbon_balance * c_price

    product_revenue = (ord_p1 * 220)+(ord_p2 * 160)
    night_salary_cost = night_hour_sum * nw
    day_work_h = (np.sum(time_array[-1]) - night_hour_sum)
    day_salary_cost = day_work_h * dw
    defect_rate_total = d_def + n_dr * night_hour_sum
    rework_loss = (ord_p1+ord_p2)* defect_rate_total * rw_cost
    flex_cost = 0 if not enable_flex else flex_int_cost
    total_profit = product_revenue - elec_cost_dayahead - elec_cost_real - deviation_cost \
                   + carbon_income - night_salary_cost - day_salary_cost - rework_loss - flex_cost

    # 生成日前报价单表格
    quote_list = []
    for t_idx in range(TOTAL_PERIOD):
        quote_list.append({
            "仿真时刻(h)": round(t_idx*TIME_STEP,2),
            "日前计划负荷(kW)": round(load_forecast[t_idx],2),
            "申报报价(元/kWh)": round(price_dayahead[t_idx],2)
        })

    res_dict = {
        "总利润": round(total_profit,2),
        "总碳排放": round(carbon_total,2),
        "总电耗": round(np.sum(load_forecast)*TIME_STEP,2),
        "夜班总工时":round(night_hour_sum,2),
        "故障次数":fault_cnt,
        "返工次数":rework_cnt,
        "偏差考核成本":round(deviation_cost,2),
        "负荷计划": load_forecast,
        "负荷实际": load_real,
        "光伏出力": pv_power,
        "储能功率": ess_power,
        "库存P1": inventory_p1,
        "库存P2": inventory_p2,
        "甘特数据": gantt_records,
        "报价单":pd.DataFrame(quote_list)
    }
    return res_dict

def generate_pareto():
    profit_list = []
    carbon_list = []
    for w in np.linspace(0,1,12):
        sim_res = run_simulation(enable_pv=True, enable_ess=True, enable_flex=True, w_e=w, w_c=1-w)
        profit_list.append(sim_res["总利润"])
        carbon_list.append(sim_res["总碳排放"])
    return profit_list, carbon_list

# =========页面UI=========
st.title("工序级生产‑电力碳协同仿真【完整复刻Word文档全部参数】")

if run_single:
    with st.spinner("仿真计算中..."):
        sim_res = run_simulation(enable_pv, enable_ess, enable_flex)

    st.subheader("✅仿真约束校验面板（优化场景）")
    st.info(f"设备故障次数：{sim_res['故障次数']}；工序返工次数：{sim_res['返工次数']}")

    st.subheader("📈 当前场景仿真核心指标")
    col1,col2,col3,col4,col5 = st.columns(5)
    col1.metric("总利润", f"{sim_res['总利润']} 元")
    col2.metric("总碳排放", f"{sim_res['总碳排放']} tCO₂")
    col3.metric("总耗电量", f"{sim_res['总电耗']} kWh")
    col4.metric("夜班工时", f"{sim_res['夜班总工时']} h")
    col5.metric("偏差考核成本", f"{sim_res['偏差考核成本']} 元")

    st.subheader("工序排产甘特图")
    df_gantt = pd.DataFrame(sim_res["甘特数据"])
    fig_gantt = px.timeline(df_gantt, x_start="Start", x_end="Finish", y="Task", color="Resource")
    fig_gantt.update_xaxes(title="时间")
    st.plotly_chart(fig_gantt, use_container_width=True)

    st.subheader("负荷时序曲线")
    fig_plan = px.line(x=time_array, y=sim_res["负荷计划"], title="日前计划负荷曲线")
    fig_plan.update_xaxes(title="时间(h)")
    fig_plan.update_yaxes(title="功率(kW)")
    st.plotly_chart(fig_plan, use_container_width=True)

    fig_real = px.line(x=time_array, y=sim_res["负荷实际"], title="实时实际负荷曲线")
    fig_real.update_xaxes(title="时间(h)")
    fig_real.update_yaxes(title="功率(kW)")
    st.plotly_chart(fig_real, use_container_width=True)

    fig_compare = go.Figure()
    fig_compare.add_trace(go.Line(x=time_array,y=sim_res["负荷计划"],name="日前计划负荷"))
    fig_compare.add_trace(go.Line(x=time_array,y=sim_res["负荷实际"],name="实时实际负荷"))
    fig_compare.update_layout(title="计划负荷与实际负荷对比")
    st.plotly_chart(fig_compare, use_container_width=True)

    st.subheader("库存变化时序")
    fig_inv = go.Figure()
    fig_inv.add_trace(go.Line(x=time_array,y=sim_res["库存P1"], name="P1库存"))
    fig_inv.add_trace(go.Line(x=time_array,y=sim_res["库存P2"], name="P2库存"))
    fig_inv.update_layout(title="半成品库存变化")
    st.plotly_chart(fig_inv, use_container_width=True)

    st.subheader("📋日前市场虚拟电厂申报报价单")
    st.dataframe(sim_res["报价单"],use_container_width=True,height=300)

    st.subheader("帕累托多目标优化前沿（利润‑碳排放）")
    pareto_profit, pareto_carbon = generate_pareto()
    fig_pareto = px.scatter(x=pareto_carbon, y=pareto_profit, title="帕累托前沿：碳排放 VS 总利润")
    fig_pareto.update_xaxes(title="总碳排放 tCO₂")
    fig_pareto.update_yaxes(title="总利润 元")
    st.plotly_chart(fig_pareto, use_container_width=True)

if run_batch:
    st.subheader("📊 4组控制变量对照实验结果")
    scene_list = [
        {"name":"场景1 基准：无光伏无储能无柔性", "pv":False,"ess":False,"flex":False},
        {"name":"场景2 仅启用光伏", "pv":True,"ess":False,"flex":False},
        {"name":"场景3 光伏+储能", "pv":True,"ess":True,"flex":False},
        {"name":"场景4 光伏+储能+柔性调度", "pv":True,"ess":True,"flex":True},
    ]
    batch_result = []
    for s in scene_list:
        r = run_simulation(s["pv"], s["ess"], s["flex"])
        batch_result.append({
            "场景名称": s["name"],
            "总利润": r["总利润"],
            "总碳排放": r["总碳排放"],
            "总电耗": r["总电耗"],
            "偏差考核成本":r["偏差考核成本"]
        })
    df_batch = pd.DataFrame(batch_result)
    st.dataframe(df_batch, use_container_width=True)
    fig_bar_profit = px.bar(df_batch, x="场景名称", y="总利润", title="四场景总利润对比")
    st.plotly_chart(fig_bar_profit, use_container_width=True)
    fig_bar_carbon = px.bar(df_batch, x="场景名称", y="总碳排放", title="四场景总碳排放对比")
    st.plotly_chart(fig_bar_carbon, use_container_width=True)

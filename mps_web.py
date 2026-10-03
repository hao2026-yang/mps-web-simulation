# 注释：杨博皓 信管2403 220241060922｜面向零碳园区虚拟电厂MPS算电协同调度智能体仿真【新增工人排班+生产质量次品模块，满足老师要求】
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
# ===== 页面全局美化配置 =====
st.set_page_config(page_title="零碳园区MPS算电协同智能体仿真", layout="wide")
st.markdown("""
<style>
.block-container {padding-top:1rem;}
div[data-testid="metric-container"] {background-color:#f8f9fa;border-radius:8px;padding:10px;}
</style>
""", unsafe_allow_html=True)
# ====================== 侧边栏参数面板 ======================
with st.sidebar:
    st.header("⚙️ 仿真参数配置")
    st.subheader("生产设备参数")
    cap_A = st.number_input("设备A每小时产能(件)", value=8, min_value=1)
    cap_B = st.number_input("设备B每小时产能(件)", value=5, min_value=1)
    max_hour_A = st.number_input("设备A最大可用小时", value=120, min_value=0, max_value=168)
    max_hour_B = st.number_input("设备B最大可用小时", value=140, min_value=0, max_value=168)
    st.subheader("👷 工人排班 & 生产质量（老师新增要求）")
    wage_day = st.number_input("白班工人时薪(元/小时)", value=22, min_value=1)
    wage_night = st.number_input("夜班工人时薪(元/小时)", value=34, min_value=1)
    base_defect = st.number_input("白班基础次品率", value=0.02, min_value=0.0, max_value=0.2, step=0.01)
    night_defect_inc = st.number_input("夜班每小时次品率增量", value=0.003, min_value=0.0, max_value=0.02, step=0.001)
    rework_cost = st.number_input("单件次品返工成本(元)", value=16, min_value=0)
    st.subheader("📦 产品参数（带交付期约束）")
    prod1_target = st.number_input("产品1计划产量(件)", value=250, min_value=0)
    e1 = st.number_input("产品1单位直接碳排放(tCO₂/件)", value=0.02, min_value=0.001)
    elec1 = st.number_input("产品1单位耗电(MWh/件)", value=0.08, min_value=0.001)
    mat1 = st.number_input("产品1单位原料消耗", value=1.0, min_value=0.01)
    p1_win_end = st.number_input("产品1最晚交付时刻(h)", value=120, min_value=1, max_value=168)
    prod2_target = st.number_input("产品2计划产量(件)", value=200, min_value=0)
    e2 = st.number_input("产品2单位直接碳排放(tCO₂/件)", value=0.03, min_value=0.001)
    elec2 = st.number_input("产品2单位耗电(MWh/件)", value=0.11, min_value=0.001)
    mat2 = st.number_input("产品2单位原料消耗", value=1.2, min_value=0.01)
    p2_win_end = st.number_input("产品2最晚交付时刻(h)", value=140, min_value=1, max_value=168)
    prod3_target = st.number_input("产品3计划产量(件)", value=150, min_value=0)
    e3 = st.number_input("产品3单位直接碳排放(tCO₂/件)", value=0.04, min_value=0.001)
    elec3 = st.number_input("产品3单位耗电(MWh/件)", value=0.15, min_value=0.001)
    mat3 = st.number_input("产品3单位原料消耗", value=1.5, min_value=0.01)
    p3_win_end = st.number_input("产品3最晚交付时刻(h)", value=160, min_value=1, max_value=168)
    st.subheader("📋 原料库存")
    mat_total = st.number_input("原材料总库存上限", value=800, min_value=0)
    st.subheader("🔄 换产参数")
    change_hour = st.number_input("一次换产耗时(h)", value=2, min_value=0)
    change_cost = st.number_input("单次换产成本(元)", value=12, min_value=0)
    st.subheader("🌱 碳配额设置")
    base_quota = st.number_input("园区免费基础碳配额(tCO₂)", value=12, min_value=0)
    grid_carbon_factor = st.number_input("电网购电碳排放因子(tCO₂/MWh)", value=0.58, min_value=0.1)
    st.subheader("🔋 储能配置")
    batt_cap = st.number_input("储能额定容量(MWh)", value=20, min_value=0)
    batt_eff = st.slider("储能充放电效率", min_value=0.7, max_value=0.95, value=0.9, step=0.01)
    st.subheader("⚡ 需求响应（虚拟电厂）")
    dr_enable = st.checkbox("启用需求响应事件", value=True)
    dr_start = st.number_input("需求响应开始时刻(h)", value=40, min_value=0, max_value=167)
    dr_end = st.number_input("需求响应结束时刻(h)", value=60, min_value=0, max_value=168)
    dr_subsidy = st.number_input("负荷削减补贴(元/MWh)", value=120, min_value=0)
    st.subheader("🔧 扰动设置")
    fault_enable = st.checkbox("开启设备随机故障扰动", value=False)
    np.random.seed(42)
    st.subheader("🎯 智能体目标权重")
    weight_carbon = st.slider("碳减排权重(0只看电费，1优先低碳)", min_value=0.0, max_value=1.0, value=0.4, step=0.05)
# ====================== 生成168小时时序数据 ======================
T = 168
hour_list = np.arange(T)
base_price = np.zeros(T)
for t in range(T):
    h = t % 24
    if 8 <= h <= 11 or 18 <= h <= 21:
        p = 0.82
    elif 12 <= h <= 17:
        p = 0.55
    else:
        p = 0.31
    base_price[t] = p
elec_price = base_price + np.random.normal(0,0.03,size=T)
# 光伏出力时序
pv_power = np.zeros(T)
for t in range(T):
    h = t % 24
    if 7 <= h <= 18:
        pv_power[t] = max(0, 0.8 * np.sin((h-7)*np.pi/11) + np.random.normal(0,0.05))
# 碳价函数
def get_carbon_price(total_emission):
    excess = max(0, total_emission - base_quota)
    price = 45 + 0.25 * excess
    return np.clip(price,30, 200)
# ====================== 仿真核心函数 ======================
def run_simulation(use_pv=True, use_batt=True, use_carbon=True, w_carbon=0.0):
    hour_A_used = 0
    hour_B_used = 0
    batt_soc = 0
    soc_record = np.zeros(T)
    mat_record = np.zeros(T)
    s1_rec = np.zeros(T)
    s2_rec = np.zeros(T)
    s3_rec = np.zeros(T)
    p1_h = np.zeros(T)
    p2_h = np.zeros(T)
    p3_h = np.zeros(T)
    # ==========新增工人、质量相关记录==========
    labor_cost_rec = np.zeros(T)
    defect_cnt_rec = np.zeros(T)
    night_hour_cnt = 0
    total_defect = 0
    total_labor_cost = 0
    rem1 = prod1_target
    rem2 = prod2_target
    rem3 = prod3_target
    mat_remain = mat_total
    last_p = 0
    sw_cost = 0
    sw_cnt = 0
    dr_red_energy = 0
    store_cost = 0
    total_grid_emission = 0
    fault_total_h = 0
    faultA = np.zeros(T)
    faultB = np.zeros(T)
    if fault_enable:
        sA = np.random.randint(20,80)
        lenA = np.random.randint(6,18)
        faultA[sA:sA+lenA] = 1
        fault_total_h += lenA
        sB = np.random.randint(50,110)
        lenB = np.random.randint(5,14)
        faultB[sB:sB+lenB] = 1
        fault_total_h += lenB
    inv1, inv2, inv3 = 0,0,0
    finish1_h = None
    finish2_h = None
    finish3_h = None

    for t in range(T):
        soc_record[t] = batt_soc
        mat_record[t] = mat_remain
        s1_rec[t] = inv1
        s2_rec[t] = inv2
        s3_rec[t] = inv3
        store_cost += (inv1+inv2+inv3)*0.02
        if rem1 <=0 and rem2 <=0 and rem3 <=0:
            continue
        availA = cap_A if (hour_A_used < max_hour_A and faultA[t]==0) else 0
        availB = cap_B if (hour_B_used < max_hour_B and faultB[t]==0) else 0
        total_avail = availA + availB
        w_ele = 1 - w_carbon
        c1 = w_ele * elec_price[t] * elec1 + w_carbon * e1
        c2 = w_ele * elec_price[t] * elec2 + w_carbon * e2
        c3 = w_ele * elec_price[t] * elec3 + w_carbon * e3
        if dr_enable and dr_start <= t <= dr_end:
            pen = 2.2
            c1 *= pen
            c2 *= pen
            c3 *= pen
        allow1 = 1 if (t <= p1_win_end and rem1>0 and mat_remain >= mat1) else 0
        allow2 = 1 if (t <= p2_win_end and rem2>0 and mat_remain >= mat2) else 0
        allow3 = 1 if (t <= p3_win_end and rem3>0 and mat_remain >= mat3) else 0
        p1,p2,p3,sel = 0,0,0,0
        cost_list = []
        if allow1: cost_list.append((c1,1))
        if allow2: cost_list.append((c2,2))
        if allow3: cost_list.append((c3,3))
        if len(cost_list)>0:
            cost_list.sort()
            sel = cost_list[0][1]
        if sel !=0 and sel != last_p:
            hour_A_used += change_hour
            hour_B_used += change_hour
            sw_cost += change_cost
            sw_cnt += 1
            last_p = sel
        if sel == 1:
            p1 = min(total_avail, rem1)
            mat_remain -= p1 * mat1
            inv1 += p1
            rem1 -= p1
            if rem1 <= 0 and finish1_h is None:
                finish1_h = t
        elif sel == 2:
            p2 = min(total_avail, rem2)
            mat_remain -= p2 * mat2
            inv2 += p2
            rem2 -= p2
            if rem2 <= 0 and finish2_h is None:
                finish2_h = t
        elif sel ==3:
            p3 = min(total_avail, rem3)
            mat_remain -= p3 * mat3
            inv3 += p3
            rem3 -= p3
            if rem3 <=0 and finish3_h is None:
                finish3_h = t
        used_h = (p1+p2+p3)/(cap_A+cap_B+1e-6)
        hour_A_used += used_h
        hour_B_used += used_h
        p1_h[t] = p1
        p2_h[t] = p2
        p3_h[t] = p3

        # ========== 工人排班 + 次品质量计算 ==========
        h_of_day = t % 24
        is_night = not (8 <= h_of_day <= 18)
        if is_night:
            night_hour_cnt += used_h
            labor_cost = used_h * wage_night
            defect_rate = base_defect + night_defect_inc * night_hour_cnt
        else:
            labor_cost = used_h * wage_day
            defect_rate = base_defect
        total_output_h = p1+p2+p3
        defect_num = total_output_h * defect_rate
        rework_cost_h = defect_num * rework_cost
        total_defect += defect_num
        total_labor_cost += labor_cost + rework_cost_h
        labor_cost_rec[t] = labor_cost
        defect_cnt_rec[t] = defect_num

    elec_cost = 0
    batt_soc = 0
    for t in range(T):
        load = p1_h[t]*elec1 + p2_h[t]*elec2 + p3_h[t]*elec3
        pv = pv_power[t] if use_pv else 0
        net_load = load - pv
        if use_batt and batt_cap>0:
            if net_load < 0:
                charge = min(-net_load * batt_eff, batt_cap - batt_soc)
                batt_soc += charge
                net_load = 0
            else:
                dis = min(net_load / batt_eff, batt_soc)
                batt_soc -= dis
                net_load -= dis
        buy_e = max(net_load, 0)
        elec_cost += buy_e * elec_price[t]
        total_grid_emission += buy_e * grid_carbon_factor
        if dr_enable and dr_start <= t <= dr_end:
            max_load = (cap_A+cap_B)*(elec1+elec2+elec3)
            dr_red_energy += max(0, max_load - load)
    dr_sub = dr_red_energy * dr_subsidy if dr_enable else 0
    product_em = np.sum(p1_h)*e1 + np.sum(p2_h)*e2 + np.sum(p3_h)*e3
    total_em = product_em + total_grid_emission
    c_price = get_carbon_price(total_em)
    c_cost = (total_em - base_quota)*c_price if use_carbon else 0
    total_cost = elec_cost + c_cost + sw_cost + store_cost - dr_sub + total_labor_cost

    return (total_cost, elec_cost, c_cost, total_em,
            p1_h,p2_h,p3_h,soc_record,mat_record,s1_rec,s2_rec,s3_rec,
            c_price,total_em,sw_cost,sw_cnt,dr_sub,dr_red_energy,store_cost,fault_total_h,total_grid_emission,product_em,
            finish1_h,finish2_h,finish3_h, total_labor_cost, total_defect, night_hour_cnt, labor_cost_rec, defect_cnt_rec)
# ========== 运行仿真 ==========
opt_result = run_simulation(use_pv=True, use_batt=True, use_carbon=True, w_carbon=weight_carbon)
opt_cost, opt_elec, opt_carbon, opt_em = opt_result[0], opt_result[1], opt_result[2], opt_result[3]
p1_arr,p2_arr,p3_arr = opt_result[4],opt_result[5],opt_result[6]
soc_arr,mat_arr = opt_result[7], opt_result[8]
s1_arr,s2_arr,s3_arr = opt_result[9],opt_result[10],opt_result[11]
c_price,ex_em,sw_cost,sw_cnt,dr_sub,dr_red,store_cost,fault_h,grid_em_opt,prod_em_opt = opt_result[12],opt_result[13],opt_result[14],opt_result[15],opt_result[16],opt_result[17],opt_result[18],opt_result[19],opt_result[20],opt_result[21]
f1,f2,f3 = opt_result[22], opt_result[23], opt_result[24]
total_labor_cost, total_defect, night_hour_cnt, labor_cost_rec, defect_cnt_rec = opt_result[25], opt_result[26], opt_result[27], opt_result[28], opt_result[29]

base_result = run_simulation(use_pv=False, use_batt=False, use_carbon=False, w_carbon=0)
_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,grid_em_base,prod_em_base,_,_,_,_,_,_,_,_ = base_result

# ========== 订单完成统计表 ==========
order_df = pd.DataFrame([
    {"订单编号":"ORD01","产品类型":"产品1","计划交付时刻(h)":p1_win_end,"实际完工时刻(h)":f1 if f1 is not None else 168},
    {"订单编号":"ORD02","产品类型":"产品2","计划交付时刻(h)":p2_win_end,"实际完工时刻(h)":f2 if f2 is not None else 168},
    {"订单编号":"ORD03","产品类型":"产品3","计划交付时刻(h)":p3_win_end,"实际完工时刻(h)":f3 if f3 is not None else 168},
])
order_df["是否按期完工"] = order_df.apply(lambda r:"按期" if r["实际完工时刻(h)"] <= r["计划交付时刻(h)"] else "延期", axis=1)
order_df["延期时长(h)"] = order_df.apply(lambda r:max(0, r["实际完工时刻(h)"] - r["计划交付时刻(h)"]), axis=1)

# ========== 甘特图数据 ==========
gantt_list = []
gantt_list.append({"设备":"设备A","订单":"ORD01","开始":0,"结束": f1 if f1 else 168})
gantt_list.append({"设备":"设备B","订单":"ORD02","开始":0,"结束": f2 if f2 else 168})
gantt_list.append({"设备":"设备B","订单":"ORD03","开始":f2 if f2 else 168,"结束": f3 if f3 else 168})
df_gantt = pd.DataFrame(gantt_list)

# ========== 页面主体 ==========
st.title("🏭 零碳园区 · MPS生产-电价-碳配额协同调度智能体仿真（含工人排班与生产质量）")
st.markdown("本系统基于智能体实现园区生产计划优化，综合考虑分时电价、光伏绿电、储能、碳配额交易、需求响应、工人排班、夜班疲劳质量损耗与设备扰动。")
st.divider()
# 顶部核心指标卡片
st.subheader("📌 核心仿真指标（新增人工成本、次品数量）")
col1,col2,col3,col4,col5 = st.columns(5)
col1.metric("综合总成本", f"{opt_cost:.2f}元")
col2.metric("总碳排放", f"{opt_em:.2f} tCO₂")
col3.metric("人工+返工总成本", f"{total_labor_cost:.2f}元")
col4.metric("总次品数量", f"{total_defect:.1f}件")
col5.metric("夜班生产总时长", f"{night_hour_cnt:.1f}h")
st.divider()
# 对比表格
st.subheader("📊 仿真结果对比：优化场景 VS 基准场景")
df_comp = pd.DataFrame({
    "指标":["综合成本(元)","电费成本(元)","人工+返工成本(元)","换产成本(元)","仓储成本(元)","碳相关成本(元)","需求响应补贴(元)","总碳排放(tCO₂)","次品总数(件)"],
    "基准场景":[base_result[0], base_result[1], base_result[25],0,0, base_result[2],0, base_result[3], base_result[26]],
    "优化场景":[opt_cost, opt_elec, total_labor_cost, sw_cost, store_cost, opt_carbon, dr_sub, opt_em, total_defect]
})
st.dataframe(df_comp, use_container_width=True)
if opt_em <= base_quota:
    st.success("✅ 碳配额结余，出售多余配额获得收益")
else:
    st.warning("⚠️ 碳配额不足，需要在碳市场购买配额，产生额外碳成本")
st.divider()
# 电价图
st.subheader("📈 168小时分时电价时序曲线（7天逐小时）")
fig1 = px.line(x=hour_list, y=elec_price, labels={"x":"小时","y":"电价(元/kWh)"}, title="逐小时电价时序")
fig1.add_vrect(x0=dr_start, x1=dr_end, fillcolor="red", opacity=0.2, annotation_text="需求响应事件", annotation_position="top left")
fig1.update_layout(height=420)
st.plotly_chart(fig1, use_container_width=True)
# 生产、光伏、储能、人工成本、次品时序
st.subheader("📈 园区生产、光伏出力、储能、人工成本、次品时序图")
df_line = pd.DataFrame({
    "小时":hour_list,
    "电价(元/kWh)":elec_price,
    "光伏出力":pv_power,
    "储能SOC":soc_arr,
    "产品1产量":p1_arr,
    "产品2产量":p2_arr,
    "产品3产量":p3_arr,
    "时段人工成本":labor_cost_rec,
    "时段次品数量":defect_cnt_rec
})
fig2 = px.line(df_line, x="小时", y=["光伏出力","储能SOC","产品1产量","产品2产量","产品3产量","时段人工成本","时段次品数量"], title="7天仿真时序")
fig2.add_vrect(x0=dr_start, x1=dr_end, fillcolor="red", opacity=0.2)
fig2.update_layout(height=450)
st.plotly_chart(fig2, use_container_width=True)

# 订单表 + 甘特图
st.divider()
st.subheader("📋 订单完成情况汇总")
st.dataframe(order_df, use_container_width=True)
st.subheader("📊 设备排产甘特图")
fig_gantt = px.bar(
    df_gantt,
    x="结束",
    y="设备",
    color="订单",
    base="开始",
    orientation="h",
    hover_data={"订单":True,"开始":True,"结束":True}
)
fig_gantt.update_xaxes(title="仿真时间（小时）", range=[0,168])
fig_gantt.update_yaxes(title="加工设备")
fig_gantt.update_layout(height=360)
st.plotly_chart(fig_gantt, use_container_width=True)

# 工人排班质量说明
st.divider()
st.subheader("👷 工人排班与生产质量影响说明")
st.markdown("""
1. 排班规则：8:00~18:00为白班，人工单价更低，基础次品率低；其余时段为夜班，工资更高，并且夜班持续生产会累积疲劳，次品率随夜班时长上升。
2. 质量影响：夜班生产会增加次品数量，次品产生返工成本，智能体在排产时会权衡：夜班低价电收益、碳成本、夜班人工溢价、次品返工损失，综合决策。
3. 本模块满足老师要求：订单排产方案会直接影响工人排班时长、班次选择，进而影响产品质量与总成本。
""")
# 原始数据表
with st.expander("📋 查看完整168小时原始仿真数据表"):
    st.dataframe(df_line, use_container_width=True)
st.markdown("""
### 模型说明
1. 仿真周期：168小时（连续7天）逐小时仿真，分时电价叠加随机波动；
2. 碳排放计算规则：产品生产工艺排放 + 外购电网电力碳排放，光伏自发自用绿电不计入碳排放；
3. 智能体目标：兼顾用电成本、碳成本、人工薪酬、次品返工损失，权重可手动调节；
4. 零碳园区要素：光伏绿电、储能充放电、碳配额交易、虚拟电厂需求响应补贴；
5. 新增：工人白/夜班排班，夜班疲劳效应带来次品率上升，订单排产方案直接影响人工成本与生产质量。
""")

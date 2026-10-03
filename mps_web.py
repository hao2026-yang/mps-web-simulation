# 注释：杨博皓 信管2403 220241060922｜面向零碳园区虚拟电厂MPS算电协同调度智能体仿真【光伏减碳版 最终修复】
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
        elif sel == 2:
            p2 = min(total_avail, rem2)
            mat_remain -= p2 * mat2
            inv2 += p2
            rem2 -= p2
        elif sel ==3:
            p3 = min(total_avail, rem3)
            mat_remain -= p3 * mat3
            inv3 += p3
            rem3 -= p3

        used_h = (p1+p2+p3)/(cap_A+cap_B+1e-6)
        hour_A_used += used_h
        hour_B_used += used_h
        p1_h[t] = p1
        p2_h[t] = p2
        p3_h[t] = p3

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
    # 产品直接工艺碳排放
    product_em = np.sum(p1_h)*e1 + np.sum(p2_h)*e2 + np.sum(p3_h)*e3
    # 总碳排放 = 产品工艺排放 + 外购电网电力排放，光伏自发用电无碳排
    total_em = product_em + total_grid_emission
    c_price = get_carbon_price(total_em)
    c_cost = (total_em - base_quota)*c_price if use_carbon else 0
    total_cost = elec_cost + c_cost + sw_cost + store_cost - dr_sub
    return (total_cost, elec_cost, c_cost, total_em,
            p1_h,p2_h,p3_h,soc_record,mat_record,s1_rec,s2_rec,s3_rec,
            c_price,total_em,sw_cost,sw_cnt,dr_sub,dr_red_energy,store_cost,fault_total_h,total_grid_emission,product_em)

# ========== 运行仿真 ==========
opt_result = run_simulation(use_pv=True, use_batt=True, use_carbon=True, w_carbon=weight_carbon)
opt_cost, opt_elec, opt_carbon, opt_em = opt_result[0], opt_result[1], opt_result[2], opt_result[3]
p1_arr,p2_arr,p3_arr = opt_result[4],opt_result[5],opt_result[6]
soc_arr,mat_arr = opt_result[7], opt_result[8]
s1_arr,s2_arr,s3_arr = opt_result[9],opt_result[10],opt_result[11]
c_price,ex_em,sw_cost,sw_cnt,dr_sub,dr_red,store_cost,fault_h,grid_em_opt,prod_em_opt = opt_result[12],opt_result[13],opt_result[14],opt_result[15],opt_result[16],opt_result[17],opt_result[18],opt_result[19],opt_result[20],opt_result[21]

base_result = run_simulation(use_pv=False, use_batt=False, use_carbon=False, w_carbon=0)
# 修正：全部22个元素，补齐下划线
_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,grid_em_base,prod_em_base = base_result

# ========== 页面主体 ==========
st.title("🏭 零碳园区 · MPS生产-电价-碳配额协同调度智能体仿真")
st.markdown("本系统基于智能体实现园区生产计划优化，综合考虑分时电价、光伏绿电、储能、碳配额交易、需求响应与设备扰动，对比基准场景与优化场景的经济效益与碳排放。")
st.divider()

# 顶部核心指标卡片
st.subheader("📌 核心仿真指标")
col1,col2,col3,col4 = st.columns(4)
col1.metric("优化场景综合净收益", f"{-opt_cost:.2f}元", f"{(base_result[0] - opt_cost):.2f}元")
col2.metric("总碳排放", f"{opt_em:.2f} tCO₂", f"{base_result[3] - opt_em:.2f} tCO₂")
col3.metric("需求响应补贴", f"{dr_sub:.2f}元")
col4.metric("换产次数", f"{sw_cnt}次")
st.divider()

# 对比表格
st.subheader("📊 仿真结果对比：优化场景 VS 基准场景")
df_comp = pd.DataFrame({
    "指标":["综合成本(元)","电费成本(元)","换产成本(元)","仓储成本(元)","碳相关成本(元)","需求响应补贴(元)","总碳排放(tCO₂)"],
    "基准场景(无光伏无储能无碳交易)":[base_result[0], base_result[1],0,0, base_result[2],0, base_result[3]],
    "优化场景(光伏+储能+碳配额+VPP+仓储+扰动)":[opt_cost, opt_elec, sw_cost, store_cost, opt_carbon, dr_sub, opt_em]
})
st.dataframe(df_comp, use_container_width=True)

# 碳提示
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

# 生产、光伏、储能多曲线
st.subheader("📈 园区生产、光伏出力、储能SOC时序图")
df_line = pd.DataFrame({
    "小时":hour_list,
    "电价(元/kWh)":elec_price,
    "光伏出力":pv_power,
    "储能SOC":soc_arr,
    "产品1产量":p1_arr,
    "产品2产量":p2_arr,
    "产品3产量":p3_arr
})
fig2 = px.line(df_line, x="小时", y=["光伏出力","储能SOC","产品1产量","产品2产量","产品3产量"], title="7天仿真时序")
fig2.add_vrect(x0=dr_start, x1=dr_end, fillcolor="red", opacity=0.2)
fig2.update_layout(height=450)
st.plotly_chart(fig2, use_container_width=True)

# 原始数据表
with st.expander("📋 查看完整168小时原始仿真数据表"):
    st.dataframe(df_line, use_container_width=True)

st.markdown("""
### 模型说明
1. 仿真周期：168小时（连续7天）逐小时仿真，分时电价叠加随机波动；
2. 碳排放计算规则：**产品生产工艺排放 + 外购电网电力碳排放，光伏自发自用绿电不计入碳排放**；
3. 智能体目标：兼顾用电成本与碳排放量，权重可手动调节；
4. 零碳园区要素：光伏绿电、储能充放电、碳配额交易、虚拟电厂需求响应补贴；
5. 扰动：支持开启设备随机故障，测试调度方案鲁棒性。
""")

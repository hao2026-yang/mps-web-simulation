# 注释：杨博皓 信管2403 220241060922｜V5细粒度单工序排产｜多级BOM、工艺路线、设备互斥、工序强先后依赖
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px

st.set_page_config(page_title="零碳园区MPS细粒度工序级协同仿真", layout="wide")
st.markdown("""
<style>
.block-container {padding-top:1rem;}
div[data-testid="metric-container"] {background-color:#f8f9fa;border-radius:8px;padding:10px;}
</style>
""", unsafe_allow_html=True)

# ======================侧边栏参数面板======================
with st.sidebar:
    st.header("⚙️ 仿真参数配置")
    st.subheader("🏭生产设备参数")
    cap_A = st.number_input("设备A产能(件/小时)", value=8, min_value=1)
    cap_B = st.number_input("设备B产能(件/小时)", value=5, min_value=1)
    cap_C = st.number_input("设备C产能(件/小时)", value=6, min_value=1)
    max_hour_A = st.number_input("设备A最大可用小时", value=120, min_value=0, max_value=168)
    max_hour_B = st.number_input("设备B最大可用小时", value=140, min_value=0, max_value=168)
    max_hour_C = st.number_input("设备C最大可用小时", value=130, min_value=0, max_value=168)

    st.subheader("👷 工人排班 & 生产质量")
    wage_day = st.number_input("白班工人时薪(元/小时)", value=22, min_value=1)
    wage_night = st.number_input("夜班工人时薪(元/小时)", value=34, min_value=1)
    base_defect = st.number_input("白班基础次品率", value=0.02, min_value=0.0, max_value=0.2, step=0.01)
    night_defect_inc = st.number_input("夜班每小时次品率增量", value=0.003, min_value=0.0, max_value=0.02, step=0.001)
    rework_cost = st.number_input("单件次品返工成本(元)", value=16, min_value=0)

    st.subheader("📦订单数量（半成品+成品）")
    p1_qty = st.number_input("半成品P1计划产量(件)", value=120, min_value=0)
    p2_qty = st.number_input("半成品P2计划产量(件)", value=100, min_value=0)
    fa_qty = st.number_input("成品FA计划产量(件)", value=80, min_value=0)
    fb_qty = st.number_input("成品FB计划产量(件)", value=60, min_value=0)

    st.subheader("⏰交付期约束(h)")
    t_p1_end = st.number_input("P1最晚交付", value=70, min_value=1, max_value=168)
    t_p2_end = st.number_input("P2最晚交付", value=80, min_value=1, max_value=168)
    t_fa_end = st.number_input("FA最晚交付", value=130, min_value=1, max_value=168)
    t_fb_end = st.number_input("FB最晚交付", value=150, min_value=1, max_value=168)

    st.subheader("🧾BOM物料消耗")
    bom_fa_p1 = st.number_input("每件FA消耗P1", value=1.0, min_value=0.1)
    bom_fb_p1 = st.number_input("每件FB消耗P1", value=1.0, min_value=0.1)
    bom_fb_p2 = st.number_input("每件FB消耗P2", value=1.0, min_value=0.1)

    st.subheader("⚙️产品能耗&碳排放 /件")
    e_p1 = st.number_input("P1总碳排放(tCO₂/件)", value=0.020, min_value=0.001)
    elec_p1 = st.number_input("P1总耗电(MWh/件)", value=0.08, min_value=0.001)
    e_p2 = st.number_input("P2总碳排放(tCO₂/件)", value=0.025, min_value=0.001)
    elec_p2 = st.number_input("P2总耗电(MWh/件)", value=0.09, min_value=0.001)
    e_fa = st.number_input("FA总碳排放(tCO₂/件)", value=0.030, min_value=0.001)
    elec_fa = st.number_input("FA总耗电(MWh/件)", value=0.10, min_value=0.001)
    e_fb = st.number_input("FB总碳排放(tCO₂/件)", value=0.035, min_value=0.001)
    elec_fb = st.number_input("FB总耗电(MWh/件)", value=0.12, min_value=0.001)

    st.subheader("🔄换产参数")
    change_hour = st.number_input("一次换产耗时(h)", value=2, min_value=0)
    change_cost = st.number_input("单次换产成本(元)", value=12, min_value=0)

    st.subheader("🌱碳配额设置")
    base_quota = st.number_input("园区免费基础碳配额(tCO₂)", value=18, min_value=0)
    grid_carbon_factor = st.number_input("电网购电碳排放因子(tCO₂/MWh)", value=0.58, min_value=0.1)

    st.subheader("🔋储能配置")
    batt_cap = st.number_input("储能额定容量(MWh)", value=20, min_value=0)
    batt_eff = st.slider("储能充放电效率", min_value=0.7, max_value=0.95, value=0.9, step=0.01)

    st.subheader("⚡需求响应（虚拟电厂）")
    dr_enable = st.checkbox("启用需求响应事件", value=True)
    dr_start = st.number_input("需求响应开始时刻(h)", value=40, min_value=0, max_value=167)
    dr_end = st.number_input("需求响应结束时刻(h)", value=60, min_value=0, max_value=168)
    dr_subsidy = st.number_input("负荷削减补贴(元/MWh)", value=120, min_value=0)

    st.subheader("🔧扰动设置")
    fault_enable = st.checkbox("开启设备随机故障扰动", value=False)
    np.random.seed(42)

    st.subheader("🎯智能体目标权重")
    weight_carbon = st.slider("碳减排权重(0只看电费，1优先低碳)", min_value=0.0, max_value=1.0, value=0.4, step=0.05)

T = 168
hour_list = np.arange(T)
base_price = np.zeros(T)
for t in range(T):
    h = t % 24
    if 8 <= h <= 11 or 18 <= h <=21:
        p = 0.82
    elif 12 <= h <=17:
        p =0.55
    else:
        p=0.31
    base_price[t]=p
elec_price = base_price + np.random.normal(0,0.03,size=T)

pv_power = np.zeros(T)
for t in range(T):
    h = t%24
    if 7<=h<=18:
        pv_power[t] = max(0,0.8*np.sin((h-7)*np.pi/11)+np.random.normal(0,0.05))

def get_carbon_price(total_emission):
    excess = max(0, total_emission - base_quota)
    price = 45 + 0.25*excess
    return np.clip(price,30,200)

# =====================【细粒度工艺路线定义】====================
# 每个产品：[{工序名称，绑定设备，本工序分摊碳排放、分摊耗电}]
process_route = {
    "P1":[
        {"proc_name":"切割","dev":"A","e_share":0.5,"elec_share":0.5},
        {"proc_name":"打磨","dev":"B","e_share":0.5,"elec_share":0.5}
    ],
    "P2":[
        {"proc_name":"下料","dev":"B","e_share":0.35,"elec_share":0.35},
        {"proc_name":"钻孔","dev":"C","e_share":0.35,"elec_share":0.35},
        {"proc_name":"粗磨","dev":"A","e_share":0.30,"elec_share":0.30}
    ],
    "FA":[
        {"proc_name":"装配FA","dev":"C","e_share":0.5,"elec_share":0.5},
        {"proc_name":"质检FA","dev":"A","e_share":0.5,"elec_share":0.5}
    ],
    "FB":[
        {"proc_name":"组合装配FB","dev":"C","e_share":0.34,"elec_share":0.34},
        {"proc_name":"表面处理FB","dev":"B","e_share":0.33,"elec_share":0.33},
        {"proc_name":"终检包装FB","dev":"A","e_share":0.33,"elec_share":0.33}
    ]
}

# 产品总能耗映射
prod_total_energy = {
    "P1":{"e":e_p1,"elec":elec_p1},
    "P2":{"e":e_p2,"elec":elec_p2},
    "FA":{"e":e_fa,"elec":elec_fa},
    "FB":{"e":e_fb,"elec":elec_fb}
}

# =====================仿真主函数【工序级】====================
def run_simulation(use_pv=True, use_batt=True, use_carbon=True, w_carbon=0.0):
    # 设备已使用工时
    dev_used_h = {"A":0.0,"B":0.0,"C":0.0}
    dev_max_h = {"A":max_hour_A,"B":max_hour_B,"C":max_hour_C}
    dev_cap = {"A":cap_A,"B":cap_B,"C":cap_C}

    batt_soc = 0.0
    soc_record = np.zeros(T)
    labor_cost_rec = np.zeros(T)
    defect_cnt_rec = np.zeros(T)
    night_hour_cnt = 0.0
    total_defect = 0.0
    total_labor_cost = 0.0

    # 库存：半成品/成品
    inv = {"P1":0.0,"P2":0.0,"FA":0.0,"FB":0.0}
    # 剩余产出目标
    remain_target = {"P1":p1_qty,"P2":p2_qty,"FA":fa_qty,"FB":fb_qty}
    # 订单完工时刻
    finish_time = {"P1":None,"P2":None,"FA":None,"FB":None}

    sw_cost, sw_cnt = 0.0, 0
    dr_red_energy = 0.0
    store_cost = 0.0
    total_grid_emission = 0.0

    # 故障
    fault = {"A":np.zeros(T),"B":np.zeros(T),"C":np.zeros(T)}
    fault_total_h =0
    if fault_enable:
        for d in ["A","B","C"]:
            s = np.random.randint(20,90)
            l = np.random.randint(4,16)
            fault[d][s:s+l]=1
            fault_total_h += l

    # 设备上一道加工产品（用于判断换产）
    dev_last_prod = {"A":None,"B":None,"C":None}
    gantt_records = []

    # 产量时序
    prod_hour = {
        "P1":np.zeros(T),"P2":np.zeros(T),"FA":np.zeros(T),"FB":np.zeros(T)
    }

    for t in range(T):
        soc_record[t] = batt_soc
        store_cost += sum(inv.values()) * 0.02
        all_done = all(remain_target[p]<=1e-3 for p in remain_target)
        if all_done:
            continue

        # 设备本小时是否故障
        dev_fault_flag = {
            d: bool(fault[d][t]>0) for d in ["A","B","C"]
        }
        w_ele = 1.0 - w_carbon

        # ======【生成全部可开工候选工序】======
        candidates = []
        for prod_id in ["P1","P2","FA","FB"]:
            rt = remain_target[prod_id]
            if rt <= 1e-3:
                continue
            # 交付期硬约束
            dl = {
                "P1":t_p1_end,"P2":t_p2_end,"FA":t_fa_end,"FB":t_fb_end
            }[prod_id]
            if t>dl:
                continue
            # BOM齐套判断：成品第一道工序需要半成品
            bom_ok = True
            if prod_id=="FA":
                bom_ok = inv["P1"] >= bom_fa_p1 * rt
            if prod_id=="FB":
                bom_ok = (inv["P1"] >= bom_fb_p1*rt) and (inv["P2"] >= bom_fb_p2*rt)
            if not bom_ok:
                continue
            route = process_route[prod_id]
            for proc_idx, proc_info in enumerate(route):
                dev = proc_info["dev"]
                # 设备故障 / 工时耗尽
                if dev_fault_flag[dev]:
                    continue
                if dev_used_h[dev] >= dev_max_h[dev]:
                    continue
                # 计算本工序综合代价
                e_unit = prod_total_energy[prod_id]["e"] * proc_info["e_share"]
                elec_unit = prod_total_energy[prod_id]["elec"] * proc_info["elec_share"]
                cost_eval = w_ele * elec_unit * elec_price[t] + w_carbon * e_unit
                if dr_enable and dr_start <= t <= dr_end:
                    cost_eval *= 2.2
                candidates.append({
                    "prod":prod_id,
                    "proc_idx":proc_idx,
                    "proc_name":proc_info["proc_name"],
                    "dev":dev,
                    "cost":cost_eval
                })
        # 按代价升序排序，贪心选最优
        candidates.sort(key=lambda x:x["cost"])

        # 遍历候选，分配给设备
        selected = {"A":None,"B":None,"C":None}
        used_qty_dev = {"A":0.0,"B":0.0,"C":0.0}
        for cand in candidates:
            d = cand["dev"]
            if selected[d] is not None:
                continue
            selected[d] = cand

        # =========执行各设备工序============
        for dev in ["A","B","C"]:
            sel_cand = selected[dev]
            if sel_cand is None:
                continue
            prod = sel_cand["prod"]
            pname = sel_cand["proc_name"]
            qty_max_dev = dev_cap[dev]
            actual_qty = min(qty_max_dev, remain_target[prod])
            # 换产判断
            if dev_last_prod[dev] is not None and dev_last_prod[dev] != prod:
                dev_used_h[dev] += change_hour
                sw_cost += change_cost
                sw_cnt += 1
            dev_last_prod[dev] = prod
            # 记录甘特细粒度：本小时工序
            gantt_records.append({
                "设备":dev,
                "产品":prod,
                "工序":pname,
                "开始":t,
                "结束":t+1
            })
            # 产出
            remain_target[prod] -= actual_qty
            inv[prod] += actual_qty
            prod_hour[prod][t] += actual_qty
            used_qty_dev[dev] += actual_qty
            dev_used_h[dev] += 1.0

            # 判断产品全部完成，记录完工时刻
            if remain_target[prod] <=1e-3 and finish_time[prod] is None:
                finish_time[prod] = t

        # =========工人班次+次品核算============
        total_produce_qty = sum(used_qty_dev.values())
        used_h_total = total_produce_qty/(cap_A+cap_B+cap_C+1e-6)
        h_of_day = t %24
        is_night = not (8 <= h_of_day <= 18)
        if is_night:
            night_hour_cnt += used_h_total
            labor_cost_h = used_h_total * wage_night
            defect_rate = base_defect + night_defect_inc * night_hour_cnt
        else:
            labor_cost_h = used_h_total * wage_day
            defect_rate = base_defect
        defect_num_h = total_produce_qty * defect_rate
        rework_h_cost = defect_num_h * rework_cost
        total_defect += defect_num_h
        total_labor_cost += labor_cost_h + rework_h_cost
        labor_cost_rec[t] = labor_cost_h
        defect_cnt_rec[t] = defect_num_h

    # =========能源仿真============
    elec_cost =0.0
    batt_soc =0.0
    for t in range(T):
        load = prod_hour["P1"][t]*elec_p1 + prod_hour["P2"][t]*elec_p2 + prod_hour["FA"][t]*elec_fa + prod_hour["FB"][t]*elec_fb
        pv = pv_power[t] if use_pv else 0
        net_load = load - pv
        if use_batt and batt_cap>1e-6:
            if net_load < 0:
                chg = min(-net_load * batt_eff, batt_cap - batt_soc)
                batt_soc += chg
                net_load = 0
            else:
                dis = min(net_load / batt_eff, batt_soc)
                batt_soc -= dis
                net_load -= dis
        buy_e = max(net_load, 0.0)
        elec_cost += buy_e * elec_price[t]
        total_grid_emission += buy_e * grid_carbon_factor
        if dr_enable and dr_start <= t <= dr_end:
            max_load = (cap_A+cap_B+cap_C)*0.15
            dr_red_energy += max(0, max_load-load)
    dr_sub = dr_red_energy * dr_subsidy if dr_enable else 0
    prod_emission = np.sum(prod_hour["P1"])*e_p1 + np.sum(prod_hour["P2"])*e_p2 + np.sum(prod_hour["FA"])*e_fa + np.sum(prod_hour["FB"])*e_fb
    total_emission = prod_emission + total_grid_emission
    carbon_price = get_carbon_price(total_emission)
    carbon_cost = (total_emission - base_quota)*carbon_price if use_carbon else 0.0
    total_cost = elec_cost + carbon_cost + sw_cost + store_cost - dr_sub + total_labor_cost

    return (
        total_cost, elec_cost, carbon_cost, total_emission,
        prod_hour["P1"],prod_hour["P2"],prod_hour["FA"],prod_hour["FB"],
        soc_record, labor_cost_rec, defect_cnt_rec,
        carbon_price, sw_cost, sw_cnt, dr_sub, dr_red_energy, store_cost, fault_total_h, total_grid_emission, prod_emission,
        finish_time, total_labor_cost, total_defect, night_hour_cnt, gantt_records
    )

# =========运行仿真========
opt_result = run_simulation(use_pv=True,use_batt=True,use_carbon=True,w_carbon=weight_carbon)
opt_cost,opt_elec,opt_carbon,opt_em = opt_result[0],opt_result[1],opt_result[2],opt_result[3]
p1_arr,p2_arr,fa_arr,fb_arr = opt_result[4],opt_result[5],opt_result[6],opt_result[7]
soc_arr, labor_rec, defect_rec = opt_result[8],opt_result[9],opt_result[10]
c_price_opt,sw_cost_opt,sw_cnt_opt,dr_sub_opt,dr_red_opt,store_cost_opt,fault_h_opt,grid_em_opt,prod_em_opt = opt_result[11],opt_result[12],opt_result[13],opt_result[14],opt_result[15],opt_result[16],opt_result[17],opt_result[18],opt_result[19]
finish_dict = opt_result[20]
total_labor_opt,total_defect_opt,night_h_opt,gantt_raw = opt_result[21],opt_result[22],opt_result[23],opt_result[24]

base_result = run_simulation(use_pv=False,use_batt=False,use_carbon=False,w_carbon=0.0)

# =========订单完成表========
order_df = pd.DataFrame([
    {"订单ID":"P1","物料类型":"半成品P1","计划交付h":t_p1_end,"实际完工h":finish_dict["P1"] if finish_dict["P1"] is not None else 168},
    {"订单ID":"P2","物料类型":"半成品P2","计划交付h":t_p2_end,"实际完工h":finish_dict["P2"] if finish_dict["P2"] is not None else 168},
    {"订单ID":"FA","物料类型":"成品FA","计划交付h":t_fa_end,"实际完工h":finish_dict["FA"] if finish_dict["FA"] is not None else 168},
    {"订单ID":"FB","物料类型":"成品FB","计划交付h":t_fb_end,"实际完工h":finish_dict["FB"] if finish_dict["FB"] is not None else 168},
])
order_df["是否按期完工"] = order_df.apply(lambda r:"按期" if r["实际完工h"] <= r["计划交付h"] else "延期",axis=1)
order_df["延期时长h"] = order_df.apply(lambda r:max(0,r["实际完工h"]-r["计划交付h"]),axis=1)

df_gantt = pd.DataFrame(gantt_raw)

# =========页面主体========
st.title("🏭零碳园区MPS【细粒度工序级】协同仿真｜多级BOM+工艺路线")

# ==========网页展示工艺路线表格【新增】==========
st.subheader("📋 产品工艺路线（生产工序顺序）")
route_data = [
    {"产品":"P1(半成品)","工序1":"切割【设备A】","工序2":"打磨【设备B】","工序3":"无","工序4":"无"},
    {"产品":"P2(半成品)","工序1":"下料【设备B】","工序2":"钻孔【设备C】","工序3":"粗磨【设备A】","工序4":"无"},
    {"产品":"FA(成品)","工序1":"装配FA【设备C】","工序2":"质检FA【设备A】","工序3":"无","工序4":"无"},
    {"产品":"FB(成品)","工序1":"组合装配FB【设备C】","工序2":"表面处理FB【设备B】","工序3":"终检包装FB【设备A】","工序4":"无"},
]
df_route = pd.DataFrame(route_data)
st.dataframe(df_route, use_container_width=True)
st.info("说明：生产必须严格按从左到右的工序顺序执行（串行工艺路线），上一道工序完成，才能执行下一道工序。成品开工前需要满足BOM半成品库存齐套约束。")
# =============================================

st.markdown("逐小时工序级排产，设备互斥，严格遵循工艺先后顺序；集成光伏储能、碳配额、VPP虚拟电厂、白夜班工人排班与次品返工。")
st.divider()

st.subheader("📌核心仿真指标")
c1,c2,c3,c4,c5 = st.columns(5)
c1.metric("综合总成本",f"{opt_cost:.2f}元")
c2.metric("总碳排放",f"{opt_em:.2f} tCO₂")
c3.metric("人工+返工总成本",f"{total_labor_opt:.2f}元")
c4.metric("总次品数量",f"{total_defect_opt:.1f}件")
c5.metric("夜班总工时",f"{night_h_opt:.1f} h")
st.divider()

st.subheader("📊优化场景VS基准场景对比")
df_comp = pd.DataFrame({
    "指标":["综合成本(元)","电费成本(元)","人工返工成本(元)","换产成本(元)","仓储成本(元)","碳成本(元)","需求响应补贴(元)","总碳排放(tCO₂)","次品总数(件)"],
    "基准场景":[base_result[0],base_result[1],base_result[21],base_result[12],base_result[16],base_result[2],base_result[14],base_result[3],base_result[22]],
    "优化场景":[opt_cost,opt_elec,total_labor_opt,sw_cost_opt,store_cost_opt,opt_carbon,dr_sub_opt,opt_em,total_defect_opt]
})
st.dataframe(df_comp,use_container_width=True)
if opt_em <= base_quota:
    st.success("✅碳配额结余，可出售配额获取收益")
else:
    st.warning("⚠️碳配额不足，需要碳市场购买配额")
st.divider()

st.subheader("📈168小时分时电价时序")
fig1 = px.line(x=hour_list,y=elec_price,labels={"x":"小时","y":"电价(元/kWh)"},title="逐小时分时电价")
fig1.add_vrect(x0=dr_start,x1=dr_end,fillcolor="red",opacity=0.2,annotation_text="需求响应事件")
fig1.update_layout(height=380)
st.plotly_chart(fig1,use_container_width=True)

st.subheader("📈生产时序曲线（半成品+成品每小时产出）")
df_line = pd.DataFrame({
    "小时":hour_list,
    "电价":elec_price,
    "光伏出力":pv_power,
    "储能SOC":soc_arr,
    "P1产量":p1_arr,
    "P2产量":p2_arr,
    "FA产量":fa_arr,
    "FB产量":fb_arr,
    "时段人工成本":labor_rec,
    "时段次品数":defect_rec
})
fig2 = px.line(df_line,x="小时",y=["光伏出力","储能SOC","P1产量","P2产量","FA产量","FB产量","时段人工成本","时段次品数"])
fig2.add_vrect(x0=dr_start,x1=dr_end,fillcolor="red",opacity=0.2)
fig2.update_layout(height=440)
st.plotly_chart(fig2,use_container_width=True)

st.divider()
st.subheader("📋半成品&成品订单完成汇总表")
st.dataframe(order_df,use_container_width=True)

st.subheader("📊细粒度工序排产甘特图（每一条代表一道工序）")
if len(df_gantt)>0:
    fig_g = px.bar(df_gantt,
                   x="结束",
                   y="设备",
                   color="产品",
                   base="开始",
                   orientation="h",
                   hover_data=["产品","工序","开始","结束"])
    fig_g.update_xaxes(title="仿真时间（小时）",range=[0,168])
    fig_g.update_layout(height=460)
    st.plotly_chart(fig_g,use_container_width=True)
else:
    st.info("暂无工序排产记录")

with st.expander("📋查看完整168小时原始仿真数据表"):
    st.dataframe(df_line,use_container_width=True)

st.markdown("""
### 模型说明
1.仿真周期168小时（7天），小时粒度**细粒度工序级排产**；
2.工艺约束：每台设备同一时刻只能执行一道工序；成品开工前校验BOM半成品物料齐套；产品严格按照预设工艺路线进行加工；
3.多约束集合：订单交付截止期、设备工时上限、工序切换产生换产时间与成本；光伏储能充放电仿真、碳配额交易、虚拟电厂需求响应；区分白夜班排班，夜班疲劳累积带来次品与返工成本；
4.求解策略：贪婪启发智能体，逐小时计算候选工序综合代价，优先选择综合代价最低的工序投入加工；
5.原型系统，用于课程设计推演，不等同于商用工业MPS软件。
""")

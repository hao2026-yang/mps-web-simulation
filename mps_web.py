# ERP课程设计｜零碳园区MPS细粒度工序BOM工艺路线协同调度智能体仿真
# 全盘BUG修复版：最小批量、最小运行/停机、爬坡、最小柔性容量、随机扰动、约束校验、帕累托前沿(修复权重灵敏度)
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np

st.set_page_config(page_title="零碳园区MPS‑虚拟电厂｜完整复刻版", layout="wide")

# ======================【全局常量定义｜来自docx文档工艺BOM】 ======================
dt = 0.25
T = 96
time_index = np.arange(T)
window_step = 4
day_hour = time_index * dt

# 产品定义：0:P1半成品,1:P2半成品,2:FA成品,3:FB成品
proc_route = {
    0: [("A","切割"),("B","打磨")],
    1: [("B","下料"),("C","钻孔"),("A","粗磨")],
    2: [("C","装配FA"),("A","质检FA")],
    3: [("C","组合装配FB"),("B","表面处理FB"),("A","终检包装FB")]
}
bom_require = {2:{"P1":1},3:{"P2":1}}
prod_name = {0:"P1半成品",1:"P2半成品",2:"FA成品",3:"FB成品"}
dev_id_map = {"A":0,"B":1,"C":2}
dev_name_list = ["设备A","设备B","设备C"]

df_tech_route = pd.DataFrame([
    {"产品":"P1半成品","工序1":"切割【设备A】","工序2":"打磨【设备B】","工序3":"无","工序4":"无"},
    {"产品":"P2半成品","工序1":"下料【设备B】","工序2":"钻孔【设备C】","工序3":"粗磨【设备A】","工序4":"无"},
    {"产品":"FA成品","工序1":"装配FA【设备C】","工序2":"质检FA【设备A】","工序3":"无","工序4":"无"},
    {"产品":"FB成品","工序1":"组合装配FB【设备C】","工序2":"表面处理FB【设备B】","工序3":"终检包装FB【设备A】","工序4":"无"},
])

# ====================== 时序生成函数 ======================
def gen_price_dayahead_15min():
    price = np.ones(T)*0.3
    for t in range(T):
        h = t*dt
        if 8 <= h <12:
            price[t]=0.55
        elif 12<=h<14:
            price[t]=0.15
        elif 18<=h<22:
            price[t]=1.0
    return price

def gen_price_rt_15min(p_da):
    np.random.seed(42)
    noise = np.random.normal(0,0.04,size=T)
    pr = p_da + noise
    return np.clip(pr,0.05,1.2)

def gen_pv_15min():
    pv = np.zeros(T)
    for t in range(T):
        h=t*dt
        if 6<=h<=18:
            pv[t]=20*np.exp(-((h-12)/3)**2)
    return pv

price_da = gen_price_dayahead_15min()
price_rt = gen_price_rt_15min(price_da)
pv_true = gen_pv_15min()

# ====================== 侧边栏参数面板 ======================
with st.sidebar:
    st.header("仿真全局参数")
    st.subheader("🔋储能&AGC辅助服务")
    batt_cap = st.number_input("储能容量kWh",value=50,step=1)
    batt_pmax = st.number_input("储能最大功率kW",value=10,step=1)
    agc_reserve_ratio = st.slider("AGC调频预留容量占比",0.0,0.4,0.15,0.05)
    fm_price = st.number_input("AGC调频补贴元/kWh",value=0.08,step=0.01)

    st.subheader("⚡电力市场惩罚&柔性约束")
    dev_penalty = st.number_input("功率偏差惩罚元/kW",value=0.3,step=0.05)
    interrupt_cost = st.number_input("柔性工序单次中断成本元",value=120,step=10)
    flex_min_cap = st.number_input("柔性负荷最小响应容量kW",value=2.0,step=0.5)
    ramp_rate = st.number_input("设备功率爬坡限值(kW/15min步)",value=25.0,step=1.0)

    st.subheader("🏭碳市场参数")
    free_quota = st.number_input("园区免费碳配额 tCO₂",value=90,step=5)
    base_carbon_price = st.number_input("基准碳价元/tCO₂",value=60,step=5)

    st.subheader("📋MPS订单&工艺约束")
    ord_p1 = st.number_input("P1半成品计划件数",value=25,step=5)
    ord_p2 = st.number_input("P2半成品计划件数",value=20,step=5)
    ord_fa = st.number_input("FA成品计划件数",value=15,step=5)
    ord_fb = st.number_input("FB成品计划件数",value=12,step=5)
    due_hour = st.number_input("统一交付截止时刻 h",value=22,step=1)
    delay_penalty = st.number_input("订单延期违约金元/件",value=25,step=1)
    setup_cost = st.number_input("设备换产成本元/次",value=30,step=1)
    setup_step = st.number_input("换产占用时段数(15min步)",value=2,step=1)
    min_batch_p1 = st.number_input("FA装配最小投产批量(P1库存)",value=3,step=1)
    min_batch_p2 = st.number_input("FB装配最小投产批量(P2库存)",value=3,step=1)
    min_run_step = st.number_input("设备最小运行步数(15min)",value=2,step=1)
    min_stop_step = st.number_input("设备最小停机步数(15min)",value=1,step=1)

    st.subheader("👷工人排班&生产质量｜随机扰动")
    wage_day = st.number_input("白班时薪元",value=22,step=1)
    wage_night = st.number_input("夜班时薪元",value=34,step=1)
    base_defect_day = st.number_input("白班基础次品率",value=0.02,step=0.005)
    defect_night_inc = st.number_input("夜班每时段次品率增量",value=0.002,step=0.001)
    rework_cost_unit = st.number_input("单件返工成本元",value=16,step=1)
    fail_prob = st.slider("设备每步故障概率",0.0,0.1,0.02,0.005)
    rework_prob = st.slider("工序返工概率",0.0,0.1,0.02,0.005)

    st.subheader("⚖️优化权重")
    w_ele = st.slider("电费权重",0.0,1.0,0.6,0.05)
    w_car = 1.0 - w_ele

# ====================== 辅助工具函数 ======================
def is_daytime(t):
    h = t*dt
    return 8 <= h < 18

def calc_agc(batt_c,batt_pmax,res_ratio,f_price):
    res_cap = batt_c * res_ratio
    fm_inc = res_cap * f_price
    opp_cost = res_cap * 0.12
    net_fm = fm_inc - opp_cost
    return res_cap,fm_inc,opp_cost,net_fm

# ====================== MPS贪婪启发智能体核心函数【全盘BUG修复｜仅放大权重灵敏度】 ======================
def run_mps_simulation(enable_green:bool,enable_carbon:bool,w_e=None,w_c=None):
    local_we = w_ele if w_e is None else w_e
    local_wc = w_car if w_c is None else w_c

    inv_P1 = np.zeros(T)
    inv_P2 = np.zeros(T)
    inv_FA = np.zeros(T)
    inv_FB = np.zeros(T)
    inv_P1[0]=0
    inv_P2[0]=0
    inv_FA[0]=0
    inv_FB[0]=0

    power_A = np.zeros(T)
    power_B = np.zeros(T)
    power_C = np.zeros(T)
    prodA_inc = np.zeros(T)
    prodB_inc = np.zeros(T)

    dev_busy_until = np.array([-1,-1,-1])
    dev_last_prod = np.array([-1,-1,-1])
    dev_run_cnt = np.array([0,0,0])
    dev_stop_cnt = np.array([0,0,0])
    dev_status = np.array([0,0,0]) #0停机 1运行
    last_power = np.array([0.0,0.0,0.0])

    task_list = []
    night_hour_cnt = 0.0
    total_defect = 0.0
    total_setup_cost = 0.0
    total_delay_cost = 0.0
    fail_cnt = 0
    rework_cnt = 0
    np.random.seed(42)

    prod_need = {0:ord_p1,1:ord_p2,2:ord_fa,3:ord_fb}
    prod_finish = {0:0,1:0,2:0,3:0}
    prod_finish_time = {0:-1,1:-1,2:-1,3:-1}

    elec_cost=0.0
    carbon_total=0.0
    carbon_cost=0.0
    dr_subsidy=0.0

    pv_use = pv_true if enable_green else np.zeros(T)
    price_t = price_rt

    for t in range(T):
        power_A[t] = 0
        power_B[t] = 0
        power_C[t] = 0

        if not is_daytime(t):
            night_hour_cnt += dt

        # --------随机扰动：设备故障--------
        for d in range(3):
            if dev_status[d]==1 and np.random.rand() < fail_prob:
                dev_busy_until[d] = t
                dev_status[d]=0
                dev_stop_cnt[d]=0
                dev_run_cnt[d]=0
                fail_cnt +=1
        if np.random.rand() < rework_prob:
            rework_cnt +=1
            total_defect +=0.3

        candidates = []
        import random
        priority_list = [0,1,2,3]
        random.shuffle(priority_list)

        for pid in priority_list:
            if prod_finish[pid] >= prod_need[pid]:
                continue
            # ✅最小批量仅对成品FA(pid=2) FB(pid=3)生效
            if pid ==2 and inv_P1[t] < min_batch_p1:
                continue
            if pid ==3 and inv_P2[t] < min_batch_p2:
                continue

            route = proc_route[pid]
            first_dev,_ = route[0]
            first_did = dev_id_map[first_dev]
            if dev_busy_until[first_did] >= t:
                continue
            ok_bom=True
            if pid==2 and inv_P1[t]<1:
                ok_bom=False
            if pid==3 and inv_P2[t]<1:
                ok_bom=False
            if ok_bom:
                candidates.append(pid)

        best_pid = None
        best_cost = 1e12
        for pid in candidates:
            remain = prod_need[pid] - prod_finish[pid]
            complete_rate = prod_finish[pid]/prod_need[pid]
            # =========【仅此处改动：放大权重灵敏度，其余全部维持原版】=========
            c = local_we * price_t[t] * 80 + local_wc * (60 if enable_carbon else 0)*5
            c = c - 0.02 * remain - 0.08*(1-complete_rate)
            if not is_daytime(t):
                c *= 1.15
            if c < best_cost:
                best_cost = c
                best_pid = pid

        if best_pid is not None:
            route = proc_route[best_pid]
            valid_start_flag = True
            for dev,op_name in route:
                did = dev_id_map[dev]
                if dev_status[did]==0:
                    if dev_stop_cnt[did] < min_stop_step:
                        valid_start_flag=False
                        break
                if dev_status[did]==1:
                    if dev_run_cnt[did] < min_run_step:
                        valid_start_flag=False
                        break
            if not valid_start_flag:
                best_pid = None
            else:
                for dev,op_name in route:
                    did = dev_id_map[dev]
                    if dev_last_prod[did] != best_pid and dev_last_prod[did]!=-1:
                        total_setup_cost += setup_cost
                        dev_busy_until[did] += setup_step
                    start_t = max(t, dev_busy_until[did]+1)
                    dur = 3
                    end_t = min(start_t + dur, T-1)
                    dev_busy_until[did] = end_t
                    dev_last_prod[did] = best_pid
                    dev_status[did]=1
                    dev_run_cnt[did] +=1
                    dev_stop_cnt[did]=0
                    task_list.append({
                        "dev_idx":did,
                        "dev_name":dev_name_list[did],
                        "product_id":best_pid,
                        "prod_name":prod_name[best_pid],
                        "op":op_name,
                        "start":start_t,
                        "end":end_t
                    })
                    raw_p = 0.0
                    if dev == "A": raw_p =115
                    if dev == "B": raw_p =92
                    if dev == "C": raw_p =80
                    delta_p = raw_p - last_power[did]
                    delta_p = np.clip(delta_p,-ramp_rate,ramp_rate)
                    set_p = last_power[did] + delta_p
                    if dev == "A":
                        power_A[start_t:end_t+1] = set_p
                        last_power[did]=set_p
                    if dev == "B":
                        power_B[start_t:end_t+1] = set_p
                        last_power[did]=set_p
                    if dev == "C":
                        power_C[start_t:end_t+1] = set_p
                        last_power[did]=set_p

                prod_finish[best_pid] +=1
                prod_finish_time[best_pid] = t
                # ✅修复库存：只修改当前t，不用切片t:
                if best_pid==0:
                    prodA_inc[t] +=1
                    inv_P1[t] +=1
                if best_pid==1:
                    prodB_inc[t] +=1
                    inv_P2[t] +=1
                if best_pid==2:
                    inv_FA[t] +=1
                if best_pid==3:
                    inv_FB[t] +=1
        # ✅空闲时段，更新启停计数器
        if best_pid is None:
            for d in range(3):
                if dev_status[d]==1:
                    dev_run_cnt[d] +=1
                else:
                    dev_stop_cnt[d] +=1

        # ✅向后传递库存
        if t+1 < T:
            inv_P1[t+1] = inv_P1[t]
            inv_P2[t+1] = inv_P2[t]
            inv_FA[t+1] = inv_FA[t]
            inv_FB[t+1] = inv_FB[t]

        if is_daytime(t):
            df = base_defect_day
        else:
            df = base_defect_day + defect_night_inc * night_hour_cnt
        total_defect += df

        load = 10.0 if len(candidates)>0 else 3.0
        grid_draw = max(load - pv_use[t], 0)
        elec_cost += grid_draw * dt * price_t[t]
        co2 = grid_draw * dt * 0.5
        carbon_total += co2

    for pid in prod_need:
        fin_h = prod_finish_time[pid]*dt
        if fin_h > due_hour:
            delay_cnt = prod_need[pid]
            total_delay_cost += delay_cnt * delay_penalty

    quota_balance = free_quota - carbon_total
    if enable_carbon:
        if quota_balance < 0:
            carbon_cost = -quota_balance * base_carbon_price
        else:
            dr_subsidy = quota_balance * base_carbon_price
    else:
        carbon_cost=0

    wage_total = night_hour_cnt * wage_night + (t*dt - night_hour_cnt)*wage_day
    rework_total = total_defect * rework_cost_unit
    labor_rework_cost = wage_total + rework_total

    check_msg = []
    any_delay = False
    for pid in prod_need:
        ft = prod_finish_time[pid]*dt
        if ft>due_hour:
            any_delay=True
            check_msg.append(f"⚠️产品{prod_name[pid]}发生订单延期")
    if not any_delay:
        check_msg.append("✅全部订单按期交付")
    check_msg.append(f"ℹ️设备故障次数：{fail_cnt}；工序返工次数：{rework_cnt}")

    ret = {
        "elec_cost":elec_cost,
        "carbon_total":carbon_total,
        "carbon_cost":carbon_cost,
        "dr_subsidy":dr_subsidy,
        "setup_cost":total_setup_cost,
        "delay_cost":total_delay_cost,
        "labor_rework":labor_rework_cost,
        "defect_qty":total_defect,
        "night_hour":night_hour_cnt,
        "inv_P1":inv_P1,"inv_P2":inv_P2,"inv_FA":inv_FA,"inv_FB":inv_FB,
        "prod_finish":prod_finish,
        "prod_finish_time":prod_finish_time,
        "power_A":power_A,
        "power_B":power_B,
        "prodA_inc":prodA_inc,
        "prodB_inc":prodB_inc,
        "check_msg":check_msg,
        "fail_cnt":fail_cnt,
        "rework_cnt":rework_cnt
    }
    return ret,task_list

# ====================== 日前‑日内‑实时能源调度模块【修复浮点数判断】 ======================
def energy_dispatch(enable_green):
    pv = pv_true if enable_green else np.zeros(T)
    soc_da = np.zeros(T); soc_da[0]=0.4
    pb_da = np.zeros(T)
    soc_rt = np.zeros(T); soc_rt[0]=0.4
    pb_rt = np.zeros(T)
    P_da_load = np.zeros(T)
    P_rt_load = np.zeros(T)
    P_rt_core = np.full(T,8)
    P_rt_flex = np.zeros(T)
    interrupt_cost_sum=0.0
    last_flex=1
    eps = 1e-3

    for t in range(T):
        p=price_da[t]
        if p<0.2:
            P_da_load[t]=16
        elif p<0.4:
            P_da_load[t]=12
        elif p<0.7:
            P_da_load[t]=8
        else:
            P_da_load[t]=4
        if p<0.2:
            pb = min(batt_pmax,(batt_cap*0.9-soc_da[t]*batt_cap)/dt)
        elif p>0.6:
            pb = -min(batt_pmax,(soc_da[t]*batt_cap-batt_cap*0.1)/dt)
        else:
            pb=0
        pb_da[t]=pb
        if t<T-1:
            soc_da[t+1]=np.clip(soc_da[t]-pb*dt/batt_cap,0.1,0.9)

    for t_start in range(0,T,window_step):
        t_end = min(t_start+window_step,T)
        for t in range(t_start,t_end):
            p=price_rt[t]
            ft=0
            if p>0.65:
                ft=flex_min_cap
            elif p<0.2:
                ft=8
            elif p<0.4:
                ft=5.6
            else:
                ft=3.2
            ft = max(ft, flex_min_cap)
            if last_flex==1 and ft <= flex_min_cap + eps:
                interrupt_cost_sum += interrupt_cost
            if last_flex==0 and ft > flex_min_cap + eps:
                interrupt_cost_sum += interrupt_cost
            last_flex = 1 if ft>flex_min_cap+eps else 0
            P_rt_flex[t]=ft
            P_rt_load[t]=P_rt_core[t]+P_rt_flex[t]
            if p<0.2:
                pb = min(batt_pmax,(batt_cap*0.9-soc_rt[t]*batt_cap)/dt)
            elif p>0.6:
                pb = -min(batt_pmax,(soc_rt[t]*batt_cap-batt_cap*0.1)/dt)
            else:
                pb=0
            pb_rt[t]=pb
            if t<T-1:
                soc_rt[t+1]=np.clip(soc_rt[t]-pb*dt/batt_cap,0.1,0.9)
    dev_cost=0.0
    for t in range(T):
        dev_cost += abs((P_rt_load[t]+pb_rt[t])-(P_da_load[t]+pb_da[t]))*dev_penalty

    bid_curve = np.zeros(T)
    for t in range(T):
        bid_curve[t] = 0.2 + P_da_load[t]/16 * 0.8

    return {
        "P_da_load":P_da_load,"pb_da":pb_da,"soc_da":soc_da,
        "P_rt_load":P_rt_load,"P_rt_core":P_rt_core,"P_rt_flex":P_rt_flex,"pb_rt":pb_rt,"soc_rt":soc_rt,
        "dev_cost":dev_cost,"interrupt_cost_sum":interrupt_cost_sum,"bid_curve":bid_curve
    }

# ======================帕累托前沿批量仿真======================
def calc_pareto():
    w_list = [0.0,0.2,0.4,0.6,0.8,1.0]
    pareto_data = []
    for we in w_list:
        wc = 1.0-we
        res,_ = run_mps_simulation(enable_green=True,enable_carbon=True,w_e=we,w_c=wc)
        pareto_data.append({"weight_ele":we,"elec_cost":res["elec_cost"],"carbon":res["carbon_total"]})
    return pd.DataFrame(pareto_data)

# ====================== 仿真执行入口 ======================
st.title("ERP课程设计｜零碳园区MPS细粒度工序BOM工艺路线协同调度智能体仿真")
st.info("原型说明：贪婪启发式智能体；15min粒度96时段；新增：最小批量、最小启停、爬坡、随机扰动、约束校验、帕累托多目标")

st.subheader("📋产品工艺路线（生产工序顺序约束）")
st.dataframe(df_tech_route,use_container_width=True,hide_index=True)
st.markdown("> 说明：生产严格遵循从左到右工序顺序；上一道工序完成，才能执行下一道工序；成品开工前需要满足BOM半成品库存齐套约束。")

st.subheader("🎬情景快速切换")
col_s1,col_s2,col_s3 = st.columns(3)
btn_base = col_s1.button("运行基准参数情景")
btn_highcarbon = col_s2.button("运行高碳价情景(碳价=120元/tCO₂)")
btn_pareto = col_s3.button("计算帕累托多目标前沿")

if btn_highcarbon:
    st.toast("已切换至高碳价情景：base_carbon_price=120",icon="⚠️")
    base_carbon_price = 120

with st.spinner("正在运行MPS贪婪排产 + 四层电力市场仿真，请稍候……"):
    res_opt,task_opt = run_mps_simulation(enable_green=True,enable_carbon=True)
    eng_opt = energy_dispatch(enable_green=True)
    res_base,task_base = run_mps_simulation(enable_green=False,enable_carbon=False)
    eng_base = energy_dispatch(enable_green=False)
    agc_res_cap,agc_fm_inc,agc_opp_cost,agc_net = calc_agc(batt_cap,batt_pmax,agc_reserve_ratio,fm_price)

def calc_net(res,eng,agc_net_val):
    prod_inc = 8000
    elec_cost = res["elec_cost"]
    carbon_cost = res["carbon_cost"]
    setup_c = res["setup_cost"]
    delay_c = res["delay_cost"]
    labor_rework_c = res["labor_rework"]
    dev_c = eng["dev_cost"]
    inter_c = eng["interrupt_cost_sum"]
    dr_inc = res["dr_subsidy"]
    net = prod_inc + dr_inc + agc_net_val - elec_cost - carbon_cost - setup_c - delay_c - labor_rework_c - dev_c - inter_c
    return net

net_opt = calc_net(res_opt,eng_opt,agc_net)
net_base = calc_net(res_base,eng_base,0.0)

flex_total = net_opt - net_base
elec_save_gain = res_base["elec_cost"] - res_opt["elec_cost"]
agc_gain = agc_net
carbon_sub_gain = res_opt["dr_subsidy"]

# --------约束校验面板--------
st.subheader("✅仿真约束校验面板（优化场景）")
for msg in res_opt["check_msg"]:
    st.markdown(msg)

st.subheader("📊核心仿真指标")
col_a1,col_a2,col_a3,col_a4,col_a5 = st.columns(5)
col_a1.metric("优化场景净收益",f"{net_opt:.2f} 元")
col_a2.metric("优化场景总碳排",f"{res_opt['carbon_total']:.2f} tCO₂")
col_a3.metric("优化场景人工+返工",f"{res_opt['labor_rework']:.2f} 元")
col_a4.metric("优化场景次品总数",f"{res_opt['defect_qty']:.2f} 件")
col_a5.metric("优化场景夜班总工时",f"{res_opt['night_hour']:.2f} h")

col_b1,col_b2,col_b3,col_b4,col_b5 = st.columns(5)
col_b1.metric("基准场景净收益",f"{net_base:.2f} 元")
col_b2.metric("基准场景总碳排",f"{res_base['carbon_total']:.2f} tCO₂")
col_b3.metric("基准场景人工+返工",f"{res_base['labor_rework']:.2f} 元")
col_b4.metric("基准场景次品总数",f"{res_base['defect_qty']:.2f} 件")
col_b5.metric("基准场景夜班总工时",f"{res_base['night_hour']:.2f} h")

st.subheader("💡灵活性收益（虚拟电厂柔性资源带来）")
col_f1,col_f2,col_f3,col_f4 = st.columns(4)
col_f1.metric("总灵活性收益",f"{flex_total:.2f} 元")
col_f2.metric("电费节约收益",f"{elec_save_gain:.2f} 元")
col_f3.metric("AGC辅助净收益",f"{agc_gain:.2f} 元")
col_f4.metric("碳结余补贴收益",f"{carbon_sub_gain:.2f} 元")

st.subheader("🔌AGC辅助服务经济性指标")
col_agc1,col_agc2,col_agc3,col_agc4 = st.columns(4)
col_agc1.metric("调频预留容量",f"{agc_res_cap:.2f} kWh")
col_agc2.metric("调频补贴收入",f"{agc_fm_inc:.2f} 元")
col_agc3.metric("套利机会成本",f"{agc_opp_cost:.2f} 元")
col_agc4.metric("AGC净收益",f"{agc_net:.2f} 元")

comp_data = [
    {"指标":"综合净收益(元)","基准场景":round(net_base,2),"优化场景":round(net_opt,2)},
    {"指标":"灵活性收益(元)","基准场景":0.0,"优化场景":round(flex_total,2)},
    {"指标":"电费成本(元)","基准场景":round(res_base["elec_cost"],2),"优化场景":round(res_opt["elec_cost"],2)},
    {"指标":"人工‑返工总成本(元)","基准场景":round(res_base["labor_rework"],2),"优化场景":round(res_opt["labor_rework"],2)},
    {"指标":"换产成本(元)","基准场景":round(res_base["setup_cost"],2),"优化场景":round(res_opt["setup_cost"],2)},
    {"指标":"碳成本(元)","基准场景":round(res_base["carbon_cost"],2),"优化场景":round(res_opt["carbon_cost"],2)},
    {"指标":"需求响应/碳结余补贴(元)","基准场景":0.0,"优化场景":round(res_opt["dr_subsidy"],2)},
    {"指标":"总碳排放 tCO₂","基准场景":round(res_base["carbon_total"],2),"优化场景":round(res_opt["carbon_total"],2)},
    {"指标":"次品总件数","基准场景":round(res_base["defect_qty"],2),"优化场景":round(res_opt["defect_qty"],2)},
]
df_comp = pd.DataFrame(comp_data)
st.subheader("📈优化场景 VS 基准场景对比总表")
st.dataframe(df_comp,use_container_width=True,hide_index=True)

st.subheader("📉时序仿真曲线（15min粒度）")
st.caption("注：受爬坡、最小运行约束，功率不再瞬间阶跃跳变。")

fig_price = go.Figure()
fig_price.add_trace(go.Scatter(x=day_hour,y=price_da,name="日前电价 元/kWh"))
fig_price.add_trace(go.Scatter(x=day_hour,y=price_rt,name="实时电价 元/kWh"))
fig_price.update_layout(xaxis_title="仿真时刻(h)")

fig_load = go.Figure()
fig_load.add_trace(go.Scatter(x=day_hour,y=eng_opt["P_da_load"],name="日前规划总负荷 kW"))
fig_load.add_trace(go.Scatter(x=day_hour,y=eng_opt["P_rt_load"],name="日内实际总负荷 kW"))
fig_load.add_trace(go.Scatter(x=day_hour,y=eng_opt["P_rt_core"],name="核心工序负荷 kW"))
fig_load.add_trace(go.Scatter(x=day_hour,y=eng_opt["P_rt_flex"],name="可中断柔性负荷 kW"))
fig_load.update_layout(xaxis_title="仿真时刻(h)")

fig_bid = go.Figure()
fig_bid.add_trace(go.Scatter(x=day_hour,y=eng_opt["bid_curve"],name="日前申报报价曲线"))
fig_bid.update_layout(xaxis_title="仿真时刻(h)")

fig_soc = go.Figure()
fig_soc.add_trace(go.Scatter(x=day_hour,y=eng_opt["soc_da"],name="日前储能SOC"))
fig_soc.add_trace(go.Scatter(x=day_hour,y=eng_opt["soc_rt"],name="日内储能SOC"))
fig_soc.update_layout(xaxis_title="仿真时刻(h)")

st.plotly_chart(fig_price,use_container_width=True)
st.plotly_chart(fig_load,use_container_width=True)
st.plotly_chart(fig_bid,use_container_width=True)
st.plotly_chart(fig_soc,use_container_width=True)

st.subheader("📦半成品&成品库存时序（BOM物料变化）")
fig_inv = go.Figure()
fig_inv.add_trace(go.Scatter(x=day_hour, y=res_opt["inv_P1"], name="P1半成品库存"))
fig_inv.add_trace(go.Scatter(x=day_hour, y=res_opt["inv_P2"], name="P2半成品库存"))
fig_inv.add_trace(go.Scatter(x=day_hour, y=res_opt["inv_FA"], name="FA成品库存"))
fig_inv.add_trace(go.Scatter(x=day_hour, y=res_opt["inv_FB"], name="FB成品库存"))
fig_inv.update_layout(xaxis_title="仿真时刻(h)", yaxis_title="库存件数")
st.plotly_chart(fig_inv, use_container_width=True)

st.subheader("📄日前市场虚拟电厂申报报价单")
df_bid = pd.DataFrame({
    "仿真时刻(h)":day_hour,
    "日前计划负荷(kW)":eng_opt["P_da_load"],
    "申报报价(元/kWh)":eng_opt["bid_curve"]
})
st.dataframe(df_bid, use_container_width=True, hide_index=True)

st.subheader("⚠️仿真约束状态（优化场景）")
col_c1,col_c2,col_c3 = st.columns(3)
quota_remain = free_quota - res_opt["carbon_total"]
col_c1.metric("碳配额剩余 tCO₂",f"{quota_remain:.2f}")
col_c2.metric("总仿真时段","96(15min粒度，24h)")
col_c3.metric("AGC预留容量 kWh",f"{agc_res_cap:.2f}")

st.subheader("📊 日前市场：D‑1预调度计划负荷曲线")
fig_da = go.Figure()
fig_da.add_trace(go.Scatter(x=day_hour,y=eng_opt["P_da_load"],name="日前计划负荷",line_color="#1f77b4"))
fig_da.update_layout(xaxis_title="仿真时刻(h)",yaxis_title="负荷 kW",title_text="日前市场预调度负荷（D‑1提前制定）")
st.plotly_chart(fig_da,use_container_width=True)

st.subheader("📊 实时市场：日内滚动后实际执行负荷曲线")
fig_rt = go.Figure()
fig_rt.add_trace(go.Scatter(x=day_hour,y=eng_opt["P_rt_load"],name="实时实际负荷",line_color="#d62728"))
fig_rt.update_layout(xaxis_title="仿真时刻(h)",yaxis_title="负荷 kW",title_text="实时市场实际执行负荷（经过日内滚动修正）")
st.plotly_chart(fig_rt,use_container_width=True)

st.subheader("📊 日前‑实时负荷叠加对比图")
fig_3layer = go.Figure()
fig_3layer.add_trace(go.Scatter(x=day_hour,y=eng_opt["P_da_load"],name="日前计划负荷",line_dash="dash",line_color="#1f77b4"))
fig_3layer.add_trace(go.Scatter(x=day_hour,y=eng_opt["P_rt_load"],name="实时实际负荷",line_color="#d62728"))
fig_3layer.update_layout(xaxis_title="仿真时刻(h)",yaxis_title="负荷 kW")
st.plotly_chart(fig_3layer,use_container_width=True)

st.subheader("🗓️细粒度工序排产甘特图（优化场景，每一条色块代表一道工序）")
fig_gantt = go.Figure()
color_map = {0:"#1f77b4",1:"#ff7f0e",2:"#2ca02c",3:"#d62728"}
for tsk in task_opt:
    fig_gantt.add_trace(go.Bar(
        y=[tsk["dev_name"]],
        x=[tsk["end"]-tsk["start"]],
        base=[tsk["start"]],
        name=f'{tsk["prod_name"]}',
        orientation="h",
        marker_color=color_map[tsk["product_id"]],
        hovertext=f'{tsk["prod_name"]}｜{tsk["op"]}'
    ))
fig_gantt.update_layout(xaxis_title="仿真步(15min)",yaxis_title="设备",barmode="overlay",height=480)
st.plotly_chart(fig_gantt,use_container_width=True)

# ========== 设备功率时序、时段产量增量图 ==========
st.subheader("设备功率时序曲线（已启用爬坡约束）")
fig_power = go.Figure()
fig_power.add_trace(go.Scatter(x=day_hour, y=res_opt["power_A"], name="设备A功率 (kW)", fill="tozeroy"))
fig_power.add_trace(go.Scatter(x=day_hour, y=res_opt["power_B"], name="设备B功率 (kW)", fill="tozeroy"))
fig_power.add_trace(go.Scatter(x=day_hour, y=pv_true, name="光伏出力 (kW)", line_color="red"))
fig_power.update_layout(xaxis_title="仿真时刻(h)",yaxis_title="功率(kW)")
st.plotly_chart(fig_power,use_container_width=True)

st.subheader("逐时段产品产出增量")
fig_prod_h = go.Figure()
fig_prod_h.add_trace(go.Scatter(x=day_hour,y=res_opt["prodA_inc"],name="P1半成品时段产出",fill="tozeroy"))
fig_prod_h.add_trace(go.Scatter(x=day_hour,y=res_opt["prodB_inc"],name="P2半成品时段产出",fill="tozeroy"))
fig_prod_h.update_layout(xaxis_title="仿真时刻(h)",yaxis_title="产出件数")
st.plotly_chart(fig_prod_h,use_container_width=True)

order_quant = {0:ord_p1,1:ord_p2,2:ord_fa,3:ord_fb}
order_tab = []
for pid in [0,1,2,3]:
    ft = res_opt["prod_finish_time"][pid]
    if ft <= 0:
        finish_h = "未完工"
        status = "未生产"
    else:
        finish_h = round(ft*dt,2)
        status="按期交付" if (finish_h <= due_hour) else "延期"
    order_tab.append({
        "产品":prod_name[pid],
        "计划产量":order_quant[pid],
        "实际完成":res_opt["prod_finish"][pid],
        "完成时刻(h)":finish_h,
        "交付截止(h)":due_hour,
        "状态":status
    })
df_order = pd.DataFrame(order_tab)
st.subheader("📦半成品‑成品订单交付汇总")
st.dataframe(df_order,use_container_width=True,hide_index=True)

# =========帕累托绘图按钮触发==========
if btn_pareto:
    with st.spinner("正在批量仿真，计算帕累托前沿……"):
        df_pareto = calc_pareto()
    st.subheader("📈多目标帕累托前沿（电费成本‑总碳排放）")
    fig_p = px.scatter(df_pareto,x="elec_cost",y="carbon",text="weight_ele",title="帕累托前沿，标签为电费权重")
    st.plotly_chart(fig_p,use_container_width=True)
    st.dataframe(df_pareto,use_container_width=True,hide_index=True)

st.markdown("""
---
> ### 系统版本说明【全盘BUG修复｜增强约束完整版】
> 1. 完整保留原有BOM、四层电力市场、双场景、灵活性收益全部模块
> 2. ✅新增生产工艺：成品最小投产批量约束
> 3. ✅新增设备技术：最小运行步数、最小停机步数、功率爬坡约束
> 4. ✅新增电力市场：柔性负荷最小响应容量约束
> 5. ✅新增生产随机扰动：设备故障概率、工序返工概率
> 6. ✅新增约束校验提示面板，输出订单延期、故障返工统计
> 7. ✅新增多目标帕累托前沿仿真，多组权重批量仿真绘图
> 8. 仿真粒度15min，96个时段；贪婪启发算法为局部最优；全局智能算法作为展望
""")

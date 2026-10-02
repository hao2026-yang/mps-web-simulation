# 杨博皓 信管2403 220241060922
# 实验：ERP-MPS辅助决策仿真智能体（园区绿电+分时电价预测，算电协同，Streamlit网页）
import streamlit as st
import pandas as pd
import random

# ====================== 页面全局美化配置 ======================
st.set_page_config(page_title="园区绿电-ERP-MPS多级智能体仿真系统", layout="wide")
# 自定义CSS美化
st.markdown("""
<style>
.main-title{
    font-size:34px;
    font-weight:bold;
    color:#1f4e79;
    text-align:center;
    margin-bottom:5px;
}
.sub-title{
    font-size:18px;
    color:#444444;
    text-align:center;
    margin-bottom:15px;
}
.arch-card{
    background-color:#e8f2fc;
    padding:12px;
    border-radius:8px;
    margin-bottom:20px;
}
.block-header{
    background-color:#f0f6fc;
    padding:10px;
    border-left:5px solid #2b7bba;
    border-radius:4px;
    font-weight:bold;
    font-size:19px;
    margin:15px 0 10px 0;
}
.metric-card{
    background:#ffffff;
    border-radius:8px;
    padding:15px;
    box-shadow: 0 1px 3px #d8e2ec;
}
</style>
""",unsafe_allow_html=True)

# ====================== 1.光伏绿电预测智能体 ======================
class GreenPowerAgent:
    def forecast_week_green_power(self, week_cnt):
        green_list = []
        for w in range(week_cnt):
            green_kwh = random.uniform(22, 38)
            green_list.append({"周次":w+1,"园区光伏绿电(kWh)":round(green_kwh,2)})
        return green_list

# ====================== 2.分时电价预测智能体 ======================
class PriceForecastAgent:
    def __init__(self):
        self.base_price = {"peak":1.56, "flat":0.94, "valley":0.50}

    def forecast_week_price(self, week_cnt):
        price_list = []
        for w in range(week_cnt):
            p_peak = self.base_price["peak"] + random.uniform(-0.08,0.08)
            p_flat = self.base_price["flat"] + random.uniform(-0.05,0.05)
            p_valley = self.base_price["valley"] + random.uniform(-0.03,0.03)
            price_list.append({
                "周次":w+1,
                "峰电价(元/kWh)":round(p_peak,3),
                "平电价(元/kWh)":round(p_flat,3),
                "谷电价(元/kWh)":round(p_valley,3)
            })
        return price_list

# ====================== 3.MPS生产智能体【修复储能用电顺序：光伏→储能→电网】 ======================
class MpsAgent:
    def __init__(self, work_hour_A_per=2, work_hour_B_per=1):
        self.work_hour_A_per = work_hour_A_per
        self.work_hour_B_per = work_hour_B_per

    def calculate(self, max_hour_A_weekly, max_hour_B_weekly, order_demand, soc_initial=0.4, price_forecast=None, green_forecast=None):
        week_num = len(order_demand)
        inventory_list = []
        soc_list = []
        inventory = 0
        soc = soc_initial
        total_A_hour = 0
        total_B_hour = 0
        total_electric_cost = 0
        total_green_used = 0
        total_surplus_green = 0
        satisfy_flag = 1

        for idx, d in enumerate(order_demand):
            produce = min(d + max(0, d - inventory), max_hour_A_weekly//self.work_hour_A_per, max_hour_B_weekly//self.work_hour_B_per)
            inventory = inventory + produce - d
            if inventory < 0:
                satisfy_flag = 0
            inventory_list.append(inventory)

            hourA = produce * self.work_hour_A_per
            hourB = produce * self.work_hour_B_per
            total_A_hour += hourA
            total_B_hour += hourB
            power_kwh = hourA * 0.08

            green_available = green_forecast[idx]["园区光伏绿电(kWh)"] if green_forecast else 0
            use_green = min(power_kwh, green_available)
            remain_power = power_kwh - use_green
            surplus_green = green_available - use_green
            total_green_used += use_green

            if surplus_green > 0:
                soc = min(0.95, soc + surplus_green / 100)
                total_surplus_green += surplus_green
            else:
                if remain_power > 0:
                    use_storage = min(remain_power, soc*100)
                    soc = soc - use_storage / 100
                    remain_power = remain_power - use_storage
                if remain_power >0 and price_forecast is not None:
                    week_price = price_forecast[idx]
                    cost = remain_power * week_price["谷电价(元/kWh)"]
                    total_electric_cost += cost
            soc = max(0.1, min(0.95, soc))
            soc_list.append(soc)

        total_green_gen = sum([x["园区光伏绿电(kWh)"] for x in green_forecast]) if green_forecast else 1
        green_self_rate = round(total_green_used / total_green_gen,3) if total_green_gen>0 else 0
        avg_inventory = sum(inventory_list)/len(inventory_list)
        if price_forecast:
            avg_valley_price = sum([x["谷电价(元/kWh)"] for x in price_forecast])/len(price_forecast)
        else:
            avg_valley_price = 0

        score = round(0.4 * satisfy_flag + 0.2 * (1 - min(avg_inventory/40, 1)) + 0.2*(1 - avg_valley_price/2)+0.2*green_self_rate,3)
        result = {
            "order_ok": satisfy_flag,
            "total_A_hour": total_A_hour,
            "total_B_hour": total_B_hour,
            "electric_cost": round(total_electric_cost,2),
            "green_self_rate": green_self_rate,
            "surplus_green": round(total_surplus_green,2),
            "inv_series": inventory_list,
            "soc_series": soc_list,
            "score": score,
            "total_green_gen": total_green_gen,
            "total_green_used": round(total_green_used,2)
        }
        return result

# ===================== 网页主体 =====================
st.markdown('<p class="main-title">园区绿电-ERP-MPS多级智能体仿真系统</p>',unsafe_allow_html=True)
st.markdown('<p class="sub-title">MPS主生产计划 · 分时电价预测 · 园区光伏绿电预测 · 储能算电协同 · 扰动重排仿真</p>',unsafe_allow_html=True)

# 系统架构卡片
st.markdown("""
<div class="arch-card">
<b>🤖 系统架构：三级协同智能体</b><br>
☀️绿电预测智能体：预测园区分布式光伏周发电量；
💡电价预测智能体：预测电网分时峰谷电价；
📦MPS生产智能体：综合产能、储能、绿电资源，优化主生产计划，实现算电协同。
<br>业务规则：用电优先级：园区光伏绿电 → 储能电池 → 外购电网电；光伏富余电量存入储能电池。
</div>
""",unsafe_allow_html=True)

# 初始化智能体
green_agent = GreenPowerAgent()
price_agent = PriceForecastAgent()
mps_agent = MpsAgent()

# 侧边栏
with st.sidebar:
    st.header("⚙️ 参数配置面板")
    st.subheader("📦 生产参数")
    order_text = st.text_input("订单需求(逗号分隔)", value="80,90,100,75", help="按周填写产品订单需求量")
    maxA = st.number_input("每周加工中心A工时上限", min_value=50, value=300, help="高耗能加工中心每周最大工时")
    maxB = st.number_input("每周装配中心B工时上限", min_value=50, value=200, help="低耗能装配中心每周最大工时")
    st.subheader("🔋 储能参数")
    soc0 = st.slider("初始SOC", min_value=0.1, max_value=0.95, value=0.4, step=0.05, help="储能电池初始容量")
    st.subheader("☀️ 园区光伏绿电模块")
    run_green = st.checkbox("启用园区绿电预测智能体", value=True)
    st.subheader("💡 电价预测模块")
    run_price = st.checkbox("启用分时电价预测智能体", value=True)
    st.subheader("🚨 扰动仿真")
    disturb_on = st.checkbox("触发扰动：紧急插单")

# 解析订单
order_list = [int(i.strip()) for i in order_text.split(",")]
week_count = len(order_list)
input_data = {"maxA":maxA,"maxB":maxB,"orders":order_list,"soc0":soc0}

# 生成绿电、电价数据
green_data = green_agent.forecast_week_green_power(week_count) if run_green else None
price_data = price_agent.forecast_week_price(week_count) if run_price else None

# 主仿真
res_base = mps_agent.calculate(maxA, maxB, order_list, soc0, price_data, green_data)

# 【新增模块：有无光伏对比仿真】
st.markdown('<div class="block-header">⚖️ 方案对比：启用光伏 VS 关闭光伏</div>',unsafe_allow_html=True)
res_no_green = mps_agent.calculate(maxA, maxB, order_list, soc0, price_data, None)
col_c1, col_c2 = st.columns(2)
with col_c1:
    st.subheader("✅ 启用园区光伏")
    st.metric("外购电费(元)", res_base["electric_cost"])
    st.metric("绿电自用率", res_base["green_self_rate"])
    st.metric("园区余电(kWh)", res_base["surplus_green"])
    st.metric("综合得分", res_base["score"])
with col_c2:
    st.subheader("❌ 无园区光伏")
    st.metric("外购电费(元)", res_no_green["electric_cost"])
    st.metric("绿电自用率", 0)
    st.metric("园区余电(kWh)", 0)
    st.metric("综合得分", res_no_green["score"])

# 绿电模块展示
if run_green:
    st.markdown('<div class="block-header">☀️ 绿电预测智能体输出：园区分布式光伏周发电量</div>',unsafe_allow_html=True)
    df_green = pd.DataFrame(green_data)
    cg1,cg2 = st.columns([0.4,0.6])
    with cg1:
        st.dataframe(df_green, use_container_width=True)
    with cg2:
        st.line_chart(df_green, x="周次", y="园区光伏绿电(kWh)", use_container_width=True)

# 电价模块展示
if run_price:
    st.markdown('<div class="block-header">📈 电价预测智能体输出：未来多周分时电价</div>',unsafe_allow_html=True)
    df_price = pd.DataFrame(price_data)
    cp1,cp2 = st.columns([0.4,0.6])
    with cp1:
        st.dataframe(df_price, use_container_width=True)
    with cp2:
        st.line_chart(df_price, x="周次", y=["峰电价(元/kWh)","平电价(元/kWh)","谷电价(元/kWh)"], use_container_width=True)

# 基准结果指标
st.markdown('<div class="block-header">📊 基准仿真结果汇总</div>',unsafe_allow_html=True)
m1,m2,m3,m4,m5,m6 = st.columns(6)
with m1:
    st.metric("订单交付", "全部满足" if res_base["order_ok"]==1 else "交付缺口")
with m2:
    st.metric("加工A总工时", round(res_base["total_A_hour"],1))
with m3:
    st.metric("装配B总工时", round(res_base["total_B_hour"],1))
with m4:
    st.metric("绿电自用率", res_base["green_self_rate"])
with m5:
    st.metric("园区余电(kWh)", res_base["surplus_green"])
with m6:
    st.metric("综合评价得分", res_base["score"])
if run_price:
    st.metric("预估外购电费(元)", res_base["electric_cost"])

# 库存、SOC曲线
week_x = list(range(1, week_count+1))
df_result = pd.DataFrame({
    "周次":week_x,
    "库存":res_base["inv_series"],
    "储能SOC":res_base["soc_series"]
})
st.markdown('<div class="block-header">📉 库存与储能SOC变化曲线</div>',unsafe_allow_html=True)
st.line_chart(df_result, x="周次", y=["库存","储能SOC"], use_container_width=True)

# ====== 新增：自动生成仿真解读文本 ======
st.markdown('<div class="block-header">📖 仿真结果解读</div>',unsafe_allow_html=True)
if run_green:
    total_gen = res_base["total_green_gen"]
    used_green = res_base["total_green_used"]
    text = f"""
本次仿真周期：{week_count}周，订单需求：{order_list}
园区光伏总发电量：{total_gen:.2f} kWh，生产消耗绿电：{used_green:.2f} kWh
绿电自用率：{res_base['green_self_rate']:.2f}，园区富余电量：{res_base['surplus_green']:.2f} kWh
外购电网电费：{res_base['electric_cost']:.2f} 元。
订单交付：{"全部满足" if res_base["order_ok"]==1 else "存在交付缺口"}，综合评价得分：{res_base["score"]}。

> 解读：本排产方案优先消耗园区自产光伏绿电，富余光伏电力存入储能电池；能源不足时消耗储能，最后向电网购电。降低外购市电成本，实现制造生产与园区绿电储能算电协同。
> 对比参考：关闭光伏场景下，外购电费为 {res_no_green['electric_cost']:.2f} 元。
"""
else:
    text = f"""
本次仿真周期：{week_count}周，订单需求：{order_list}
未启用园区光伏，全部用电依靠储能与外购电网电。
外购电网电费：{res_base['electric_cost']:.2f} 元。
订单交付：{"全部满足" if res_base["order_ok"]==1 else "存在交付缺口"}，综合评价得分：{res_base["score"]}。
"""
st.info(text)

# 扰动仿真模块
if disturb_on:
    st.markdown('<div class="block-header">🚨 扰动仿真：紧急插单，智能体重排计划</div>',unsafe_allow_html=True)
    disturb_orders = order_list.copy()
    disturb_orders[1] +=25
    res_disturb = mps_agent.calculate(maxA, maxB, disturb_orders, soc0, price_data, green_data)
    d1,d2 = st.columns(2)
    with d1:
        st.subheader("扰动前（基准方案）")
        st.metric("交付状态", "全部满足" if res_base["order_ok"]==1 else "交付缺口")
        st.metric("外购电费", res_base["electric_cost"])
        st.metric("综合得分", res_base["score"])
    with d2:
        st.subheader("扰动后（紧急插单）")
        st.metric("交付状态", "全部满足" if res_disturb["order_ok"]==1 else "交付缺口")
        st.metric("外购电费", res_disturb["electric_cost"])
        st.metric("综合得分", res_disturb["score"])

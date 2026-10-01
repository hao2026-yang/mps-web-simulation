# 杨博皓 信管2403 220241060922
# MPS主生产计划智能体 - Web网页交互版本 Streamlit
# 架构：感知层 → 推理层 → 决策层 → 执行输出层
import streamlit as st

class MpsAgent:
    def __init__(self):
        self.erp_order_data = None
        self.factory_config = None
        self.scheme_on_time = None
        self.scheme_cost_ctrl = None
        self.risk_level = "低风险"

    def perceive(self, erp_input, factory_param):
        """感知层：接收ERP订单数据、工厂产能与成本参数"""
        self.erp_order_data = erp_input
        self.factory_config = factory_param

    def reason(self):
        """推理层：两套排产方案数学计算"""
        order = self.erp_order_data
        fac = self.factory_config
        base_cap = fac["max_daytime_cap"]
        overtime_cap = fac["overtime_cap"]
        normal_cost = fac["normal_cost"]
        overtime_cost = fac["overtime_cost"]

        # ==========方案1：按时交货优先，允许加班==========
        total_cost_ontime = 0
        total_overtime = 0
        for week, need in order.items():
            if need > base_cap:
                over_amount = need - base_cap
                total_overtime += over_amount
                total_cost_ontime += base_cap * normal_cost + over_amount * overtime_cost
            else:
                total_cost_ontime += need * normal_cost
        self.scheme_on_time = {
            "name": "按时交货优先",
            "total_cost": round(total_cost_ontime, 2),
            "overtime": total_overtime,
            "warn": "无风险"
        }

        # ==========方案2：成本控制优先，禁止加班，允许订单延期==========
        total_cost_costctrl = 0
        total_delay = 0
        warn_list = []
        stock = 0
        for week, need in order.items():
            produce = base_cap
            total_supply = produce + stock
            if total_supply >= need:
                stock = total_supply - need
                total_cost_costctrl += produce * normal_cost
            else:
                delay_amt = need - total_supply
                total_delay += delay_amt
                warn_list.append(f"第{week}周订单延期{delay_amt}件")
                stock = 0
                total_cost_costctrl += produce * normal_cost
        warn_str = ";".join(warn_list)
        self.scheme_cost_ctrl = {
            "name": "成本控制优先",
            "total_cost": round(total_cost_costctrl, 2),
            "delay": total_delay,
            "warn": warn_str
        }

        # 风险等级判定
        delay_total = self.scheme_cost_ctrl["delay"]
        overtime_total = self.scheme_on_time["overtime"]
        if delay_total > 60 or overtime_total > 120:
            self.risk_level = "高风险"
        elif delay_total > 20 or overtime_total > 50:
            self.risk_level = "中风险"
        else:
            self.risk_level = "低风险"

    def decision(self):
        """决策层：智能体自动择优推荐"""
        planA = self.scheme_on_time
        planB = self.scheme_cost_ctrl
        cost_gap = planA["total_cost"] - planB["total_cost"]

        if planB["delay"] > 40:
            rec_name = planA["name"]
            rec_reason = f"存在大量订单延期，优先保障交付，规避客户违约风险"
        elif cost_gap > 6000:
            rec_name = planB["name"]
            rec_reason = f"成本优势显著，节约{cost_gap}元，可接受少量订单延期"
        else:
            rec_name = planA["name"]
            rec_reason = f"成本差距较小，优先保证全部订单按期交付"
        return rec_name, rec_reason

# ---------------------- Web页面UI ----------------------
st.set_page_config(page_title="MPS生产计划智能体", layout="wide")
st.title("MPS主生产计划智能体｜网页仿真系统")
st.markdown("在下方输入工厂参数与5周订单，点击【运行智能体】生成排产方案")

# 分两栏布局
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("🏭 工厂基础参数")
    max_daytime_cap = st.number_input("每周基础产能(件)", min_value=0, value=140)
    overtime_cap = st.number_input("每周加班最大产能(件)", min_value=0, value=50)
    normal_cost = st.number_input("正常生产单件成本(元)", min_value=0, value=100)
    overtime_cost = st.number_input("加班生产单件成本(元)", min_value=0, value=130)

with col_right:
    st.subheader("📦 5周订单需求")
    w1 = st.number_input("第1周订单", min_value=0, value=120)
    w2 = st.number_input("第2周订单", min_value=0, value=150)
    w3 = st.number_input("第3周订单", min_value=0, value=180)
    w4 = st.number_input("第4周订单", min_value=0, value=140)
    w5 = st.number_input("第5周订单", min_value=0, value=110)

run_btn = st.button("▶ 运行MPS智能体", type="primary")

if run_btn:
    # 组装输入数据
    factory_cfg = {
        "max_daytime_cap": max_daytime_cap,
        "overtime_cap": overtime_cap,
        "normal_cost": normal_cost,
        "overtime_cost": overtime_cost
    }
    order_data = {
        1:w1, 2:w2, 3:w3, 4:w4, 5:w5
    }
    # 实例化智能体
    agent = MpsAgent()
    agent.perceive(order_data, factory_cfg)
    agent.reason()
    rec_name, rec_reason = agent.decision()

    st.divider()
    st.subheader("📊 仿真结果")
    st.write(f"**场景风险等级：{agent.risk_level}**")

    # 输出方案表格
    data_table = [
        {
            "方案": agent.scheme_on_time["name"],
            "总成本(元)": agent.scheme_on_time["total_cost"],
            "关键指标": f"总加班工时:{agent.scheme_on_time['overtime']}",
            "预警信息": agent.scheme_on_time["warn"]
        },
        {
            "方案": agent.scheme_cost_ctrl["name"],
            "总成本(元)": agent.scheme_cost_ctrl["total_cost"],
            "关键指标": f"总延期订单:{agent.scheme_cost_ctrl['delay']}",
            "预警信息": agent.scheme_cost_ctrl["warn"]
        }
    ]
    st.dataframe(data_table, use_container_width=True)

    st.success(f"【智能体决策推荐】推荐方案：{rec_name}，依据：{rec_reason}")

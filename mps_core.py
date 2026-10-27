# -*- coding: utf-8 -*-
"""零碳园区 MPS 排产仿真内核（纯 Python，不依赖 Streamlit）。

相对原 agent_new.py 的扩展
--------------------------
1) 生产结构：三级 BOM（原材料 M1/M2/M3 → 半成品 P1/P2/P3 → 成品 FA/FB/FC），
   每个产品有独立工艺路线、设备、工时、功率。
2) 原材料与提前期：期初库存 + 到货批次（lead time），缺料时工序自动顺延，
   记录“缺料等待”，因此排产必须同时考虑备料节奏与交期。
3) 节点电价：园区设 3 个结算节点（N1 机加区 / N2 装配区 / N3 关口表），
   节点价 = 关口分时价 ×(1+网损) + 阻塞价差 × 峰谷系数；购电按各节点
   负荷占比分摊，因此“工序放在哪个节点、放在什么时段”都会改变电费。
4) 紧急订单：产品/数量/到达时刻/交期/误期罚金均可配置；急单任务不早于
   到达时刻开工，按单统计完成时刻、是否按期、对普通订单的挤占。
5) 调度解码改为“延迟重试”：前置工序或齐套未满足时任务排队等待，而不是
   像旧版那样直接丢弃（旧版 GA 能靠“丢任务”把成本做低）。

单位约定：1 步 = 15 分钟，96 步 = 24 h；功率 kW；电价 元/kWh；
碳排沿用原作业口径（Σ 购电量 kWh × 0.5），如需物理严格口径把
CARBON_UNIT_SCALE 改为 0.001。
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# ----------------------------------------------------------------------------
# 基础常量
# ----------------------------------------------------------------------------
T = 96                 # 仿真步数
STEP_H = 0.25          # 每步小时数
DUE_STEP = 88          # 普通订单交付截止 22:00
NIGHT_STEPS = set(range(0, 28)) | set(range(88, 96))   # 0-7 点 + 22-24 点
CARBON_UNIT_SCALE = 1.0   # 沿用旧版口径；改 0.001 即为“吨”的严格算法
PEAK_LIMIT = 80.0         # 园区需量阈值 kW
FREE_QUOTA = 90.0         # 免费碳配额
DELAY_PENALTY = 20.0      # 普通订单误期罚金 元/步
SEMI = ("P1", "P2", "P3")


# ----------------------------------------------------------------------------
# 主数据
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Device:
    code: str
    name: str
    node: str


DEVICES: Dict[str, Device] = {
    "A": Device("A", "下料 / 磨削中心", "N1"),
    "B": Device("B", "机加中心", "N1"),
    "C": Device("C", "装配中心", "N2"),
}

NODES: Dict[str, str] = {"N1": "机加区节点", "N2": "装配区节点", "N3": "园区关口表"}


@dataclass(frozen=True)
class Material:
    code: str
    name: str
    unit_cost: float
    init_qty: int
    batch_qty: int
    lead_h: float


MATERIALS: Dict[str, Material] = {
    "M1": Material("M1", "钢材", 5.0, 24, 40, 6.0),
    "M2": Material("M2", "铝材", 6.0, 16, 30, 8.0),
    "M3": Material("M3", "标准件", 2.0, 30, 50, 12.0),
}


@dataclass(frozen=True)
class Step:
    name: str
    device: str
    dur: int
    power: float


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    kind: str                       # semi / final
    bom: Dict[str, int]
    route: Tuple[Step, ...]


PRODUCTS: Dict[str, Product] = {
    "P1": Product("P1", "P1 半成品", "semi", {"M1": 2, "M2": 1},
                  (Step("下料", "A", 1, 15), Step("磨削", "B", 1, 10))),
    "P2": Product("P2", "P2 半成品", "semi", {"M2": 2, "M3": 1},
                  (Step("切割", "B", 1, 15), Step("钻孔", "C", 1, 20))),
    "P3": Product("P3", "P3 半成品", "semi", {"M1": 1, "M3": 2},
                  (Step("锯切", "A", 1, 12), Step("铣削", "B", 1, 18))),
    "FA": Product("FA", "FA 成品", "final", {"P1": 1, "M3": 2},
                  (Step("装配", "C", 2, 25), Step("磨削", "A", 1, 15))),
    "FB": Product("FB", "FB 成品", "final", {"P2": 1, "M2": 1},
                  (Step("装配", "C", 2, 25), Step("表面处理", "B", 2, 20))),
    "FC": Product("FC", "FC 成品", "final", {"P1": 1, "P3": 1, "M1": 1},
                  (Step("装配", "C", 3, 30), Step("抛光", "A", 2, 18))),
}

FINALS = ("FA", "FB", "FC")


# ----------------------------------------------------------------------------
# 参数与急单
# ----------------------------------------------------------------------------
@dataclass
class UrgentOrder:
    product: str = "FA"
    qty: int = 2
    arrive_h: float = 12.0
    due_h: float = 18.0
    penalty: float = 60.0          # 元/步（15 min）
    label: str = "急单"

    @property
    def arrive_step(self) -> int:
        return int(round(self.arrive_h * 4))

    @property
    def due_step(self) -> int:
        return int(round(self.due_h * 4))


@dataclass
class Params:
    plans: Dict[str, int] = field(default_factory=lambda: {
        "P1": 10, "P2": 8, "P3": 6, "FA": 4, "FB": 3, "FC": 2})
    batch_size: int = 2
    changeover_time: int = 2
    changeover_cost: float = 30.0
    day_wage: float = 22.0
    night_wage: float = 34.0
    base_defect: float = 0.02
    night_defect_inc: float = 0.02
    rework_cost: float = 16.0
    fault_prob: float = 0.02
    ramp_limit: float = 25.0
    w_ele: float = 0.6
    use_pv: bool = True
    use_storage: bool = True
    use_flex: bool = True
    carbon_price: float = 60.0
    grid_mult: float = 1.0
    congestion: float = 1.0
    lead_scale: float = 1.0
    init_scale: float = 1.0
    urgent_orders: Tuple[UrgentOrder, ...] = ()
    seed: int = 42
    node_spread: Dict[str, float] = field(default_factory=lambda: {
        "N1": 0.06, "N2": 0.12, "N3": 0.0})
    node_loss: Dict[str, float] = field(default_factory=lambda: {
        "N1": 0.02, "N2": 0.03, "N3": 0.0})

    @property
    def w_carbon(self) -> float:
        return round(1.0 - self.w_ele, 2)


# ----------------------------------------------------------------------------
# 价格 / 光伏
# ----------------------------------------------------------------------------
def build_base_price(p: Params) -> np.ndarray:
    base = np.full(T, 0.5)
    base[0:28] = 0.3
    base[28:68] = 0.9
    base[68:96] = 0.5
    return base * p.grid_mult


def build_node_prices(p: Params) -> Dict[str, np.ndarray]:
    base = build_base_price(p)
    ratio = np.clip((base - 0.3 * p.grid_mult) / max(0.6 * p.grid_mult, 1e-6), 0.0, 1.0)
    return {n: base * (1.0 + p.node_loss[n]) + p.node_spread[n] * p.congestion * ratio
            for n in NODES}


def build_pv(p: Params, peak: float = 50.0) -> np.ndarray:
    pv = np.zeros(T)
    if p.use_pv:
        pv[28:68] = np.sin(np.linspace(0, np.pi, 40)) * peak
    return pv


# ----------------------------------------------------------------------------
# 任务
# ----------------------------------------------------------------------------
@dataclass
class Task:
    id: str
    product: str
    kind: str                 # semi / batch / urgent
    seq: int
    device: str
    node: str
    dur: int
    power: float
    prev: Optional[str]
    release: int = 0
    due: int = DUE_STEP
    urgent_id: int = -1
    qty: int = 1
    needs: Dict[str, int] = field(default_factory=dict)


def _build_chain(product: str, tag: str, qty: int, release: int, due: int,
                 kind: str, urgent_id: int, tasks: Dict[str, Task]) -> List[str]:
    prod = PRODUCTS[product]
    ids: List[str] = []
    prev = None
    for i, step in enumerate(prod.route):
        tid = f"{tag}_{prod.code}_{i}"
        tasks[tid] = Task(
            id=tid, product=prod.code, kind=kind, seq=i, device=step.device,
            node=DEVICES[step.device].node, dur=step.dur, power=step.power,
            prev=prev, release=release, due=due, urgent_id=urgent_id, qty=qty,
            needs=dict(prod.bom) if i == 0 else {},
        )
        ids.append(tid)
        prev = tid
    return ids


def build_tasks(p: Params) -> Dict[str, Task]:
    tasks: Dict[str, Task] = {}
    for code in SEMI:
        for i in range(int(p.plans.get(code, 0))):
            _build_chain(code, f"{code}_{i:02d}", 1, 0, DUE_STEP, "semi", -1, tasks)

    batch = max(1, int(p.batch_size))
    for code in FINALS:
        total = int(p.plans.get(code, 0))
        made = 0
        b = 0
        while made < total:
            q = min(batch, total - made)
            _build_chain(code, f"{code}_b{b:02d}", q, 0, DUE_STEP, "batch", -1, tasks)
            made += q
            b += 1

    for u_idx, order in enumerate(p.urgent_orders):
        tag = f"U{u_idx}{order.product}"
        _build_chain(order.product, tag, int(order.qty), order.arrive_step,
                     order.due_step, "urgent", u_idx, tasks)
    return tasks


def _unit_hash(text: str) -> float:
    return (zlib.crc32(text.encode("utf-8")) % 10_000) / 10_000.0


# ----------------------------------------------------------------------------
# 结果
# ----------------------------------------------------------------------------
@dataclass
class Result:
    label: str
    total_cost: float
    ele_cost: float
    carbon: float
    carbon_cost: float
    mat_cost: float
    flex_save: float
    penalties: Dict[str, float]
    schedule: List[dict]
    node_load: Dict[str, np.ndarray]
    total_load: np.ndarray
    grid_import: np.ndarray
    soc: np.ndarray
    inv: Dict[str, np.ndarray]
    mat_stock: Dict[str, np.ndarray]
    urgent_stats: List[dict]
    missing: int
    shortage_wait: int
    makespan: int
    night_share: float
    pv_selfuse: float
    late_tasks: int
    mat_detail: Dict[str, float]
    prices: Dict[str, np.ndarray]
    base_price: np.ndarray
    pv: np.ndarray

    @property
    def delay_cost(self) -> float:
        return self.penalties["交期违约"]

    @property
    def on_time(self) -> bool:
        return all(s["on_time"] for s in self.urgent_stats) if self.urgent_stats else True

    def urgent_summary(self) -> str:
        if not self.urgent_stats:
            return "未启用急单"
        parts = []
        for s in self.urgent_stats:
            state = "按期" if s["on_time"] else f"超期 {s['late_steps']} 步"
            parts.append(f"{s['label']} 完成于 {s['finish'] * STEP_H:.2f} 时（{state}）")
        return "；".join(parts)


# ----------------------------------------------------------------------------
# 仿真器
# ----------------------------------------------------------------------------
class Simulator:
    def __init__(self, p: Params):
        self.p = p
        self.tasks = build_tasks(p)
        self.ids = list(self.tasks)
        self.prices = build_node_prices(p)
        self.base_price = build_base_price(p)
        self.pv = build_pv(p)
        self.lead = {m: min(T - 2, max(1, int(round(MATERIALS[m].lead_h * 4 * p.lead_scale))))
                     for m in MATERIALS}
        self.init_qty = {m: max(1, int(round(MATERIALS[m].init_qty * p.init_scale)))
                         for m in MATERIALS}
        self.batch_qty = {m: MATERIALS[m].batch_qty for m in MATERIALS}
        # 两批到货：提前期末 + 2 倍提前期
        self.receipts = {m: [(self.lead[m], self.batch_qty[m]),
                             (min(T - 2, self.lead[m] * 2), self.batch_qty[m])]
                         for m in MATERIALS}

    # ---------- 物料 ----------
    def _stock(self, used: Dict[str, List[Tuple[int, int]]], code: str, t: int) -> int:
        qty = self.init_qty[code]
        qty += sum(q for s, q in self.receipts[code] if s <= t)
        qty -= sum(q for s, q in used[code] if s <= t)
        return qty

    def _material_start(self, used, needs: Dict[str, int], t0: int) -> Tuple[int, bool]:
        t = t0
        shifted = False
        for code, need in needs.items():
            guard = 0
            while self._stock(used, code, t) < need:
                arrivals = [s for s, _ in self.receipts[code] if s > t]
                if not arrivals:
                    return T + 2, True
                t = min(arrivals)
                shifted = True
                guard += 1
                if guard > 3:
                    return T + 2, True
        return t, shifted

    # ---------- 半成品齐套 ----------
    @staticmethod
    def _semi_ready(prod_ev, cons_ev, code, need, t0):
        def stock(t):
            return (sum(q for e, q in prod_ev[code] if e <= t)
                    - sum(q for e, q in cons_ev[code] if e <= t))
        if stock(t0) >= need:
            return t0
        for end, _ in sorted(prod_ev[code]):
            if end > t0 and stock(end) >= need:
                return end
        return None

    # ---------- 主流程 ----------
    def run(self, order: Sequence[str], label: str = "") -> Result:
        p = self.p
        uniq = [tid for tid in dict.fromkeys(order) if tid in self.tasks]
        rest = [tid for tid in self.ids if tid not in set(uniq)]
        pending = uniq + rest

        dev_free = {d: 0 for d in DEVICES}
        dev_last = {d: None for d in DEVICES}
        dev_last_p = {d: 0.0 for d in DEVICES}
        used: Dict[str, List[Tuple[int, int]]] = {m: [] for m in MATERIALS}
        prod_ev: Dict[str, List[Tuple[int, int]]] = {s: [] for s in SEMI}
        cons_ev: Dict[str, List[Tuple[int, int]]] = {s: [] for s in SEMI}

        node_load = {n: np.zeros(T) for n in NODES}
        sche: List[dict] = []
        placed: Dict[str, dict] = {}
        pen_change = pen_delay = pen_rework = pen_short = 0.0
        flex_save = 0.0
        shortage_wait = 0
        missing = 0

        while pending:
            progressed = False
            for tid in list(pending):
                task = self.tasks[tid]
                if task.prev and task.prev not in placed:
                    continue

                start = max(dev_free[task.device], task.release)
                if task.prev:
                    start = max(start, placed[task.prev]["End"])

                # 半成品齐套：库存不足时顺延到产出时刻，仍不足则本轮跳过
                semi_need = {k: v * task.qty for k, v in task.needs.items() if k in SEMI}
                if semi_need:
                    ready = start
                    for code, need in semi_need.items():
                        r = self._semi_ready(prod_ev, cons_ev, code, need, ready)
                        if r is None:
                            ready = None
                            break
                        ready = max(ready, r)
                    if ready is None:
                        continue
                    start = ready

                if dev_last[task.device] not in (None, task.product):
                    start += p.changeover_time
                    pen_change += p.changeover_cost

                last_p = dev_last_p[task.device] if dev_last[task.device] is not None else 0.0
                actual_p = min(task.power, last_p + p.ramp_limit) if last_p else task.power

                if p.fault_prob > 0 and _unit_hash(f"{tid}|{p.seed}") < p.fault_prob:
                    start += 2

                # 柔性：非急单工序挪到“光伏富余 / 电价低”的时段
                # 边际电价 ≈ 节点价 × max(0, 1 − 光伏/负荷)；光伏能盖住负荷时近似免费
                if p.use_flex and task.kind in ("semi", "batch") and start < T - 1:
                    def margin(step: int) -> float:
                        load_now = sum(float(node_load[n][step]) for n in NODES) + actual_p
                        cover = float(self.pv[step]) / max(load_now, 1e-6)
                        return float(self.prices[task.node][step]) * max(0.0, 1.0 - cover)

                    best, best_s = start, margin(start)
                    for s in range(start, min(start + 16, T)):
                        sc = margin(s)
                        if sc < best_s - 0.02:
                            best, best_s = s, sc
                    if best > start:
                        saved = (float(self.prices[task.node][start])
                                 - float(self.prices[task.node][best])) * actual_p * STEP_H * task.dur
                        flex_save += max(0.0, saved)
                        start = best

                # 原材料齐套（只取外购物料，半成品已在上面处理）
                mat_needs = {k: v * task.qty for k, v in task.needs.items() if k in MATERIALS}
                wait = 0
                if mat_needs:
                    m_start, shifted = self._material_start(used, mat_needs, start)
                    if shifted:
                        wait = max(0, m_start - start)
                        start = m_start
                        shortage_wait += wait
                    if start > T:
                        pen_short += 120.0
                        missing += 1
                        start = T - 1
                    for code, need in mat_needs.items():
                        used[code].append((start, need))

                end = min(start + task.dur, T)
                if end > task.due and task.kind != "urgent":
                    pen_delay += (end - task.due) * DELAY_PENALTY

                for s in range(start, end):
                    node_load[task.node][s] += actual_p
                    defect = p.base_defect + (p.night_defect_inc if s in NIGHT_STEPS else 0.0)
                    pen_rework += p.rework_cost * defect

                # 半成品消耗/产出记账
                for code, need in semi_need.items():
                    cons_ev[code].append((start, need))
                if task.product in SEMI and task.seq == len(PRODUCTS[task.product].route) - 1:
                    prod_ev[task.product].append((end, task.qty))

                placed[tid] = {"Start": start, "End": end}
                dev_free[task.device] = end
                dev_last[task.device] = task.product
                dev_last_p[task.device] = actual_p
                sche.append({
                    "Task": tid, "Product": task.product, "Device": task.device,
                    "Node": task.node, "Start": start, "End": end, "Duration": end - start,
                    "Power": actual_p, "Qty": task.qty, "Kind": task.kind,
                    "Urgent": task.kind == "urgent", "Wait": wait,
                })
                pending.remove(tid)
                progressed = True

            if not progressed:      # 死锁兜底：强制投放并记缺料
                tid = pending.pop(0)
                task = self.tasks[tid]
                start = max(dev_free[task.device], task.release, T - 6)
                end = min(start + task.dur, T)
                pen_short += 120.0
                missing += 1
                node_load[task.node][start:end] += task.power
                placed[tid] = {"Start": start, "End": end}
                dev_free[task.device] = end
                dev_last[task.device] = task.product
                sche.append({
                    "Task": tid, "Product": task.product, "Device": task.device,
                    "Node": task.node, "Start": start, "End": end,
                    "Duration": end - start, "Power": task.power, "Qty": task.qty,
                    "Kind": task.kind, "Urgent": task.kind == "urgent", "Wait": 0,
                })

        return self._settle(label, sche, node_load, used, prod_ev, cons_ev,
                            pen_delay, pen_change, pen_rework, pen_short,
                            flex_save, shortage_wait, missing)

    # ---------- 结算 ----------
    def _settle(self, label, sche, node_load, used, prod_ev, cons_ev,
                pen_delay, pen_change, pen_rework, pen_short,
                flex_save, shortage_wait, missing) -> Result:
        p = self.p
        total_load = sum(node_load[n] for n in NODES)
        soc_curve = np.zeros(T)
        grid_import = np.zeros(T)
        ele_cost = 0.0
        soc = 0.5
        served_pv = 0.0

        for t in range(T):
            load = float(total_load[t])
            if p.use_storage:
                if self.base_price[t] < 0.4 and soc < 0.9:
                    ch = min(30.0, (0.9 - soc) * 100 / STEP_H)
                    soc = min(0.9, soc + ch * STEP_H / 100)
                    load += ch
                elif self.base_price[t] > 0.85 and soc > 0.1:
                    dis = min(30.0, (soc - 0.1) * 100 / STEP_H)
                    soc = max(0.1, soc - dis * STEP_H / 100)
                    load = max(0.0, load - dis)
            soc_curve[t] = soc

            served = min(self.pv[t], load)
            served_pv += served
            net = max(0.0, load - self.pv[t])
            grid_import[t] = net

            if total_load[t] > 0:
                shares = {n: float(node_load[n][t]) / float(total_load[t]) for n in NODES}
            else:
                shares = {n: 1.0 / len(NODES) for n in NODES}
            for n in NODES:
                ele_cost += float(self.prices[n][t]) * net * shares[n] * STEP_H

        energy = float(grid_import.sum() * STEP_H)
        carbon = energy * 0.5 * CARBON_UNIT_SCALE
        carbon_cost = ((carbon - FREE_QUOTA) * p.carbon_price if carbon > FREE_QUOTA
                       else -(FREE_QUOTA - carbon) * 30.0)
        pen_peak = float(sum(max(0.0, v - PEAK_LIMIT) * 5.0 for v in total_load))

        mat_detail = {m: sum(q for _, q in used[m]) * MATERIALS[m].unit_cost for m in MATERIALS}
        mat_cost = sum(mat_detail.values())

        urgent_stats = []
        for u_idx, order in enumerate(p.urgent_orders):
            ts = [e for e in sche if e["Task"].startswith(f"U{u_idx}{order.product}_")]
            if not ts:
                continue
            finish = max(e["End"] for e in ts)
            late = max(0, finish - order.due_step)
            urgent_stats.append({
                "label": order.label or f"{order.product}×{order.qty}",
                "product": order.product, "qty": order.qty,
                "arrive": order.arrive_step, "due": order.due_step,
                "finish": finish, "late_steps": late, "on_time": late == 0,
                "penalty": late * order.penalty,
                "start": min(e["Start"] for e in ts),
            })
        pen_urgent = sum(s["penalty"] for s in urgent_stats)

        total_cost = (p.w_ele * ele_cost + p.w_carbon * carbon_cost + mat_cost
                      + pen_delay + pen_change + pen_rework + pen_peak + pen_short + pen_urgent)

        # 半成品库存曲线
        inv = {}
        for s in SEMI:
            curve = np.zeros(T)
            for t in range(T):
                curve[t] = max(0.0, (sum(q for e, q in prod_ev[s] if e <= t)
                                     - sum(q for e, q in cons_ev[s] if e <= t)))
            inv[s] = curve

        mat_stock = {m: np.array([max(0, self._stock(used, m, t)) for t in range(T)], dtype=float)
                     for m in MATERIALS}

        makespan = max((e["End"] for e in sche), default=0)
        night_energy = sum(e["Power"] * e["Duration"] for e in sche
                           if any(t in NIGHT_STEPS for t in range(e["Start"], e["End"])))
        all_energy = sum(e["Power"] * e["Duration"] for e in sche) or 1.0
        pv_energy = float(self.pv.sum() * STEP_H)

        return Result(
            label=label, total_cost=total_cost, ele_cost=ele_cost, carbon=carbon,
            carbon_cost=carbon_cost, mat_cost=mat_cost, flex_save=flex_save,
            penalties={
                "交期违约": round(pen_delay + pen_urgent, 2),
                "换产成本": round(pen_change, 2),
                "返工成本": round(pen_rework, 2),
                "需量超容": round(pen_peak, 2),
                "缺料惩罚": round(pen_short, 2),
                "碳交易(负为补贴)": round(carbon_cost, 2),
                "物料成本": round(mat_cost, 2),
                "柔性节约收益": round(flex_save, 2),
            },
            schedule=sche, node_load=node_load, total_load=total_load,
            grid_import=grid_import, soc=soc_curve, inv=inv, mat_stock=mat_stock,
            urgent_stats=urgent_stats, missing=missing, shortage_wait=shortage_wait,
            makespan=makespan, night_share=night_energy / all_energy,
            pv_selfuse=served_pv / pv_energy if pv_energy > 0 else 0.0,
            late_tasks=sum(1 for e in sche if e["Kind"] != "urgent" and e["End"] > DUE_STEP),
            mat_detail={k: round(v, 2) for k, v in mat_detail.items()},
            prices=self.prices, base_price=self.base_price, pv=self.pv,
        )


# ----------------------------------------------------------------------------
# 算法
# ----------------------------------------------------------------------------
def greedy_order(tasks: Dict[str, Task]) -> List[str]:
    def key(tid):
        t = tasks[tid]
        rank = 0 if t.kind == "urgent" else (1 if t.product in FINALS else 2)
        return (rank, t.due, t.release, tid)
    return sorted(tasks, key=key)


def _ox_crossover(a: List[str], b: List[str], rng: random.Random) -> List[str]:
    n = len(a)
    i, j = sorted(rng.sample(range(n), 2))
    child: List[Optional[str]] = [None] * n
    child[i:j] = a[i:j]
    fill = [x for x in b if x not in child]
    k = 0
    for idx in range(n):
        if child[idx] is None:
            child[idx] = fill[k]
            k += 1
    return child  # type: ignore


def run_greedy(p: Params, label: str = "方案A 敏捷派") -> Result:
    sim = Simulator(p)
    return sim.run(greedy_order(sim.tasks), label=label)


def _heuristic_orders(tasks: Dict[str, Task]) -> List[List[str]]:
    """给 GA 的初始种群塞几个“像样”的解，避免纯随机起步太差。"""
    ids = list(tasks)
    by_product: List[str] = []
    for code in list(SEMI) + list(FINALS):
        by_product += [t for t in ids if tasks[t].product == code]
    by_product += [t for t in ids if t not in by_product]
    grouped = sorted(ids, key=lambda t: (tasks[t].product, tasks[t].seq))
    by_due = sorted(ids, key=lambda t: (tasks[t].kind != "urgent", tasks[t].due, tasks[t].release))
    return [greedy_order(tasks), by_product, grouped, by_due]


def _local_search(sim: "Simulator", order: List[str], passes: int = 1) -> Tuple[float, List[str]]:
    """相邻交换爬山：比整代 GA 便宜，专门修“换产过多”这类局部毛病。"""
    best = list(order)
    best_cost = sim.run(best).total_cost
    for _ in range(passes):
        improved = False
        for i in range(len(best) - 1):
            cand = best[:]
            cand[i], cand[i + 1] = cand[i + 1], cand[i]
            c = sim.run(cand).total_cost
            if c < best_cost - 1e-9:
                best, best_cost, improved = cand, c, True
        if not improved:
            break
    return best_cost, best


def run_ga(p: Params, pop_size: int = 24, generations: int = 25,
           label: str = "方案B 统筹派", seed: Optional[int] = None,
           progress=None, local_search: bool = True) -> Result:
    sim = Simulator(p)
    ids = sim.ids
    if len(ids) < 2:
        return sim.run(ids, label=label)
    rng = random.Random(p.seed if seed is None else seed)
    population = [rng.sample(ids, len(ids)) for _ in range(pop_size)]
    seeds = _heuristic_orders(sim.tasks)
    for i, order in enumerate(seeds[: min(len(seeds), pop_size)]):
        population[i] = list(order)
    best_res, best_cost = None, math.inf
    for gen in range(generations):
        scored = []
        for ind in population:
            res = sim.run(ind, label=label)
            scored.append((res.total_cost, ind, res))
            if res.total_cost < best_cost:
                best_cost, best_res = res.total_cost, res
        scored.sort(key=lambda x: x[0])
        elite = [x[1] for x in scored[: max(2, pop_size // 5)]]
        population = elite[:]
        while len(population) < pop_size:
            a, b = rng.choice(elite), rng.choice(elite)
            child = _ox_crossover(a, b, rng)
            if rng.random() < 0.3:
                i, j = rng.sample(range(len(child)), 2)
                child[i], child[j] = child[j], child[i]
            population.append(child)
        if local_search and gen % 4 == 3:
            c, order = _local_search(sim, elite[0], passes=1)
            if c < best_cost:
                best_cost, best_res = c, sim.run(order, label=label)
                elite[0] = order
                population[0] = order
        if progress:
            progress(gen + 1, generations, best_cost)
    assert best_res is not None
    best_res.label = label
    return best_res


def compare_plans(p: Params, pop_size: int = 24, generations: int = 25,
                  progress=None) -> Tuple[Result, Result]:
    return (run_greedy(p), run_ga(p, pop_size=pop_size, generations=generations,
                                  progress=progress))


# ----------------------------------------------------------------------------
# 场景工具
# ----------------------------------------------------------------------------
def with_urgent(p: Params, order: Optional[UrgentOrder]) -> Params:
    return replace(p, urgent_orders=(order,) if order else ())


def urgent_presets() -> Dict[str, Optional[UrgentOrder]]:
    return {
        "不插急单": None,
        "① FA×2｜12:00 到｜18:00 交": UrgentOrder("FA", 2, 12.0, 18.0, 60, "急单①FA×2"),
        "② FB×3｜09:00 到｜15:00 交": UrgentOrder("FB", 3, 9.0, 15.0, 60, "急单②FB×3"),
        "③ FC×1｜14:00 到｜20:00 交": UrgentOrder("FC", 1, 14.0, 20.0, 60, "急单③FC×1"),
        "④ FC×2｜10:00 到｜14:00 交（最紧）": UrgentOrder("FC", 2, 10.0, 14.0, 90, "急单④FC×2"),
    }


def control_cases() -> List[Tuple[str, dict]]:
    return [
        ("场景1 基准", dict(use_pv=False, use_storage=False, use_flex=False)),
        ("场景2 仅光伏", dict(use_pv=True, use_storage=False, use_flex=False)),
        ("场景3 光储", dict(use_pv=True, use_storage=True, use_flex=False)),
        ("场景4 光储+柔性", dict(use_pv=True, use_storage=True, use_flex=True)),
    ]


def bom_table() -> List[dict]:
    rows = []
    for code, prod in PRODUCTS.items():
        rows.append({
            "产品": code, "名称": prod.name,
            "类型": "半成品" if prod.kind == "semi" else "成品",
            "BOM 构成": " + ".join(f"{k}×{v}" for k, v in prod.bom.items()),
            "工艺路线": " → ".join(f"{s.name}({s.device},{s.dur}步,{s.power:.0f}kW)" for s in prod.route),
        })
    return rows


def material_table() -> List[dict]:
    return [{
        "物料": m.code, "名称": m.name, "单价(元/件)": m.unit_cost,
        "期初库存": m.init_qty, "到货批量": m.batch_qty, "提前期(h)": m.lead_h,
    } for m in MATERIALS.values()]

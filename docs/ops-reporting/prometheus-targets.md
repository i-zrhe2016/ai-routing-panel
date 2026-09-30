# Prometheus Targets 与 Labels

> Type: Reference
> Status: Active
> Scope: Prometheus Targets 与 Labels

## Target 设计

每个真实节点和 exporter 端点应有唯一 target。Prometheus 服务发现或静态配置必须保留稳定身份，不以易变 IP 作为报告主键。

必需标签：

| 标签 | 含义 | 示例值 |
| --- | --- | --- |
| `job` | exporter 类型 | `node`、`xray`、`blackbox` |
| `instance` | 实际抓取端点 | `host:port` |
| `node_id` | 稳定、非敏感节点 ID | `normal-01` |
| `node_role` | 节点角色 | `control_plane`、`normal_data_plane`、`ai_data_plane` |
| `environment` | 环境 | `production`、`staging` |
| `region` | 部署区域 | 受控枚举 |

禁止把 UUID、订阅 token、域名、客户端 IP、错误文本或请求路径放入标签。高基数字段既增加存储成本，也可能泄漏业务信息。

## 当前配置 Targets

权威定义是 [monitoring/prometheus/prometheus.yml](../../monitoring/prometheus/prometheus.yml)，当前包含 8 个静态 targets：

| 角色 | 抓取目标 | 稳定身份 |
| --- | --- | --- |
| 面板 | 控制面回环 `:18080/metrics` | `control-01` |
| 控制面 node-exporter / cAdvisor | 控制面回环 `:9100` / `:18081` | `control-01` |
| 普通数据面 node-exporter / cAdvisor | 普通数据面 Tailscale DNS `:19100` / `:18081` | `normal-01` |
| 台湾 AI node-exporter / cAdvisor | 台湾节点 Tailscale DNS `:9100` / `:18081` | `ai-taiwan` |
| Prometheus | `localhost:9090` | 由 Prometheus 默认 target labels 标识 |

AI Xray `/debug/vars` 不作为 Prometheus 独立 target，而由面板受控读取后通过鉴权的 `/metrics` 聚合暴露。配置中的目标数量不等同于线上抓取健康，仍须检查运行时 `/targets`。

节点资源序列必须提供稳定的 `node_id`、`node_role`、`environment` 和 `region`。当前节点身份为 `control-01`、`normal-01`、`ai-taiwan`，环境为 `production`，台湾 AI 节点地域为 `taiwan`；未确认地域使用 `unknown`。

## 查询约束

日报查询必须同时限定 `environment`、`node_id` 和预期 `job`，并验证每个节点只有一条期望序列。使用 range query 覆盖完整报告窗口；查询结果保存指标名、标签选择器、起止时间、步长和样本覆盖率，不保存原始日志。

## Target 健康门禁

上线前确认：

1. Prometheus `/targets` 中所有目标为预期地址且抓取成功；
2. `up`、节点资源、服务状态与 blackbox 指标都有稳定样本；
3. 标签集合符合允许列表，无重复 `node_id + job`；
4. 抓取间隔与规则阈值匹配，时钟同步；
5. Prometheus API 只向日报器提供只读访问，并限制网络来源。

缺少必需标签、出现重复序列或 target 超过两个抓取周期不可达时，日报必须标记数据源缺口，而不是猜测节点状态。

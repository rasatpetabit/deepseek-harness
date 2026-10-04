# DSH 上游协调实现计划

[English](2026-10-04-upstream-reconciliation.md) | 中文

> **供代理执行者使用：** 必需子技能：使用 superpowers:executing-plans 逐任务实施本计划。步骤使用复选框（`- [ ]`）跟踪。

**目标：** 产出已提交、可独立审阅的 dsh-v0.2.1-alpha.1 候选，保留局域网自动登录及远程管理，提供不可变源部署和仅局域网可访问的 HTTPS 代理模板。

**架构：** 在隔离工作树中合并固定上游版本。保留上游 cookie 认证及目标 ConfigForms；nginx 边缘强制公共浏览器准入及局域网成员资格，回环后端接受代理跳点。在生产激活前构建并测试临时配置及复制数据。

**技术栈：** TypeScript、Cordis、Node、pnpm、Vitest、pytest、nginx、systemd、restic。

**规范：** [.agents/notes/proposed/architecture/2026-10-04-upstream-reconciliation.md](../../../.agents/notes/proposed/architecture/2026-10-04-upstream-reconciliation.zh.md)

## Global Constraints

- 不可变合并目标：dsh-v0.2.1-alpha.1，完整提交 5badb15009ae1756c3afe0ae0cef1faafc290ccc。
- 源工作树：/srv/dev/ai/deepseek-harness/.worktrees/upgrade-dsh-v0.2.1-alpha.1；基线 master 为 9156b2790607f471dd6862a12b9d22d8be502a12。
- 逐字节保留主检出的 docs/OPERATIONS.md：SHA-256 a91f2a4dcb3bbce50387436cb4ae0128653ad52b0c6581efce0cf57493575f97。绝不 stash 或提交其他会话的编辑。
- 公共地址为 https://192.168.100.200 ；nginx server 层 ACL 允许 192.168.100.0/24 并拒绝所有其他客户端；后端为 127.0.0.1:3080。
- 不增加转发 IP 身份解析器、额外密钥、全局插件兼容绕过或供应商补丁；不轮换凭据、不进行付费推理、不推送上游、不集成 master、不在线激活。
- 父会话完成确切制品前沿发布审阅前，不生成证书、不在线安装或重启。浏览器信任仍是明确未解决的投产工作。
- 对本确切计划的独立审阅先于产品编辑、依赖安装或合并执行。已有设计 PASS 不代表审阅本计划。

## Review Focus

- 干净根路径自动登录不得接受带查询的请求、非 GET 请求、其他路径或伪造转发头；任务 2 负责负向测试。
- 公共 HTTPS 客户端不得把 Origin 降为 HTTP 或省略 WebSocket Origin；任务 3 负责运行时代理拒绝测试。
- 构建改变 HEAD、弄脏已跟踪源或创建未跟踪源，必须在提升前失败；任务 4 负责受控移动源测试。
- 迁移不得默默忽略未纳入盘点的历史子会话；任务 5 负责完整盘点及目录失败检查。
- 插件仅注册路由不能证明模型/推理力度选择或 Hindsight 召回/工具能力；任务 5 负责不进行推理的实际消费能力断言。

---

### Task 1: Pinned merge and minimal house delta

**文件：** 修改 package.json、pnpm-lock.yaml、packages/bundle/web-app/cordis.patch.yml、packages/bundle/web-app/src/index.ts、packages/client/connection/src/browser-auth.ts、packages/client/ui-settings/src/client/index.ts、packages/client/ui-settings-general/src/client/index.ts；按需更新所属测试及 README 双语对。

**接口：** 消费目标 WebStartup publicUrl/trustedHosts 和 ConfigForms 服务；产出合并树，保留现有 trustedNetworks 及 allowRemoteAdministration 配置，不由冲突解决引入 SettingsScope 引用。

- [ ] 写入前验证在线写入者/分支/工作树调查、基线 HEAD、带注释的 safety/pre-upgrade 引用、主检出运维哈希、工作树本地钩子配置及翻译合并驱动。
- [ ] 使用 --no-ff --no-commit 合并完整不可变目标。预测在上述文件中恰有七个内容冲突；不假定所有自动合并语义正确。
- [ ] 解决 package.json 时保留目标 Vite；仅当 git grep 证明无当前消费者时移除 unrun。从解决后的清单重新生成锁文件，不手动协调依赖块。保留钩子生成的 notices。
- [ ] 解决 web-app 组合时同时保留 allowRemoteAdministration 和 publicUrl，保留上游显示/公共 URL 行为，仅在识别插件消费者之后协调 ownerRequestPolicy。所需策略继续要求会话认证；不得依据一个旧插件删除它。
- [ ] 使用目标 ConfigForms 和 SettingsDescribeMirror 实现解决 ui-settings 持久化；通过现有明确许可及启动配置保留已认证远程主机持久化。将 ui-settings-general 文档控制器调整为 ctx.configForms.describe()，保留目标开发工具/版本槽位。
- [ ] 按上游目录相对重定向和公共 URL 语义解决 browser-auth；保留 trustedNetworks、现有凭据和 cookie 归属。不携带过时的绝对根 URL 规范化。
- [ ] 运行针对 connection、settings、settings-general、web-app 的测试。检查完整冲突解决差异，量化相对目标的内部差异。长检查前通过钩子提交合并；绝不仅凭此检查点声称候选就绪。

### Task 2: Clean-root automatic login on the accepted proxy hop

**文件：** 修改 packages/client/connection/src/browser-auth.ts 及 tests/browser-auth.host.spec.ts；更新 packages/client/connection/README.md、README.zh.md 及配对记录；在所属 web-app 测试中添加所需组合 Web 回归测试。

**接口：** BrowserAuth.authorizeIndex(req, res): boolean 保留现有令牌交换、签名 cookie 验证及重定向后返回 false 的语义。trustedNetworks 仅读取 req.socket.remoteAddress。已认证 /api 准入保持不变。

- [ ] 添加失败测试：配置的回环干净 GET / 签发 cookie 并重定向 ./；所得 cookie 认证 API；GET /?x=1、HEAD /、POST /、/index.html、/api、缺失套接字及网外套接字不自动签发。转发头不能替代可信套接字。错误令牌不触发自动登录。
- [ ] 运行所属 Vitest 文件，观察之前被接受的带查询请求导致预期失败。
- [ ] 仅为可信网络自动登录增加干净查询条件，保留上游重定向语义；使用现有签发逻辑，不新增签名或身份机制。通过 cookie 验证及凭据复用确认重新加载。
- [ ] 运行所属 Vitest 文件及组合 connection/web-app 案例。协调预期绝对根重定向的陈旧内部测试。更新本地 README/JSDoc、双语配对及决策记录；通过钩子提交。

### Task 3: Repository-owned nginx HTTPS template and procedure

**文件：** 创建 scripts/deploy/nginx-dsh.conf.template 及 scripts/tests/test_https_proxy.py；创建 docs/cookbook/deploy-house-https.md、中国语言对应文件及配对记录。不写入 /etc 或生产目录。

**接口：** 模板占位符保存操作员提供的证书/密钥路径；公共 Host/Origin 精确为 192.168.100.200 和 https://192.168.100.200 。后端为 http://127.0.0.1:3080 。所有路径使用一个 server 层 allow/deny 策略及一个转发 location。

- [ ] 添加运行实际渲染 nginx 配置的测试，而非重新实现准入谓词：根、API、资源、流及升级路径上的允许局域网与拒绝网络；错误 Host；HTTP/错误/null Origin；跨站 Fetch Metadata；没有精确 Origin 的浏览器升级；查询令牌不进入日志；伪造 Forwarded 及 X-Forwarded-* 不到达后端；Secure cookie 重写；不缓冲流及保留查询。拥有临时端口/进程并等待拆除。
- [ ] 使用 nginx 标准 TLS/proxy 指令、精确公共 Host 和 Origin map、server 层 ACL、升级转发、无查询日志格式及头移除编写模板。不增加代理身份协议或拓宽可信子网。不在测试流程中生成正式证书或安装客户端信任；测试证书仅供临时测试。
- [ ] 对临时渲染前缀运行 nginx -t 及 pytest 代理测试。缺少 nginx 二进制是验证阻碍，不能据此声称运行时代理测试通过。
- [ ] 记录尚待解决的签发者/客户端信任选择及确切激活要求，包含局域网监听拒绝、IP SAN、证书链、有效期、目标浏览器信任、原始转发 Host 及持久签名密钥保留。通过钩子提交模板、测试及文档。

### Task 4: Immutable-source deployment

**文件：** 修改 scripts/deploy-dsh.sh、scripts/tests/test_deploy_overlay.py；创建 scripts/tests/test_deploy_source.py；更新任务 3 的内部部署 cookbook 双语对。保留操作员所有的主检出运维文档。

**接口：** scripts/deploy-dsh.sh --source <absolute-canonical-worktree> --expected-sha <full-commit-SHA> 选择唯一构建/归档/输出源。Git 公共目录身份必须等于所属仓库，源必须是已注册工作树。预期 SHA 在安装、构建及暂存前后必须等于 HEAD。现有生产隔离、锁、服务健康和保留语义保持不变。

- [ ] 用真实部署脚本、临时仓库和受控外部命令替身添加失败测试：缺参数、相对/非规范源、错误仓库、错误预期 SHA、脏索引/已跟踪/未跟踪文件、构建改变 HEAD、构建修改源、构建引入未跟踪源以及正确干净源。断言拒绝案例绝不调用提升或重启。编辑脚本前运行并观察 RED。
- [ ] 生产写入前验证源身份及完整不可变 SHA。在安装/构建和输出复制前后检查完整 porcelain 清洁状态及 HEAD；git archive 预期 SHA，绝不使用 HEAD。仅覆盖明确源工作树输出，并排除 .git 文件及目录。使用现有最终版本隔离扫描。
- [ ] 运行 pytest 部署源/覆盖/配置/保留测试以及 bash -n scripts/deploy-dsh.sh。检查钩子修改的文件，广泛构建/类型检查前通过钩子提交。不调用真实部署来测试验收。

### Task 5: Scratch plugin, migration, catalog and rollback evidence

**文件：** 为拟议决策记录添加限定证据附录；可复用测试放在所属 session-persistence-jsonl 或配置测试目录。临时运行时数据位于生产外，不在报告中包含凭据。

**接口：** 消费目标受支持的 JSONL catalog/list/open 机制，包含历史子会话补充证据；不构建竞争转换器。消费插件发布元数据及已安装精确树；为父发布审阅者产出精确插件版本建议和前代完整性/恢复证据。

- [ ] 选择版本前查询当前兼容插件发布；仅在非生产端口安装和启动临时配置。不进行付费模型调用，测试 subscriptions 真实目录选择及推理力度/提供者能力，以及 Hindsight 召回/工具可用性。若无兼容发布可用，明确保留阻碍；仅有临时证明和独立审阅时提出精确版本豁免。
- [ ] 只读盘点所有生产 Session id、物理代和子会话关系；复制前哈希前代文件。在复制临时数据上对完整盘点运行受支持的只读打开（不发布）、写打开（v4 后继）、重新打开及重启，断言无缺失子会话且前代哈希不变。运行相关 catalog-migration/generation/migration-refusal 测试及已构建迁移 worker 冒烟测试。
- [ ] 对照捕获的基线比较全部七条路由的精确提供者、模型及推理力度身份集合，包含 code 9/9、deepseek 1/1 和 LiteLLM 15/15。仅数量不是验收证据。
- [ ] 找到现有 restic 备份并恢复到临时非生产目录；验证清单、锁、精确插件树、配置和受保护凭据，不输出密钥。针对恢复的数据启动匹配旧运行时。任何缺失备份内容或运行时失败都记录为激活阻碍。
- [ ] 运行改变的组合输出对应的选定无密钥快照、pnpm run build、pnpm run typecheck、pnpm run test:docs、pnpm run doc-sync、pnpm run lint，以及所属提交前技能选择的针对性 pytest/Vitest 检查。不习惯性运行完整单元/覆盖套件。记录实际退出码和关键输出；提交证据/文档，不留已暂存文件。

### Task 6: Exact-artifact handoff, not activation

**文件：** 更新 WORKLOG.md 和拟议决策记录，记录候选状态、未完成检查及审阅绑定；不改写主检出运维文档。

**接口：** 为父会话前沿发布审阅和 breaker 验收产出候选 SHA/工作树路径、变更范围指标、命令证据、负向测试、插件版本、数据完整性/恢复证据及回滚路径。本任务不授予审阅通过或生产激活。

- [ ] 重新检查主检出运维哈希、master HEAD、其他写入者/工作树、候选清洁状态及空索引；保留工作树和安全标签。报告合并修复及相对固定目标的最小差异指标。
- [ ] 部署路径为审阅和所有投产前提之后，使用已审阅候选的已提交脚本，明确规范 --source 及匹配 --expected-sha。回滚路径为暂停写入者，恢复数据/插件/设置/凭据/服务/边缘配置的一致备份，选择匹配旧版本，重启并验证目录/会话能力。此处不执行任一路径。
- [ ] 返回结构化验收报告，包含每条实际运行的 shell 命令、已提交文件、新增测试、输出、未解决风险、审阅回执及无暂存文件状态。向父会话请求缺失的必需审阅，不自行认证。

## Preparation disposition

设计自审未发现与已批准设计冲突。已通过 git merge-tree 读取七冲突预测，未修改工作树。规范的轻量建议审阅和本计划的独立审阅仍由父会话负责；子会话没有受治理的审阅启动工具，且不得派发其他代理。因此产品实现停在计划审阅前提，不能描述为已完成。

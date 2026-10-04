# 内部版本投产

[English](deploy-house-commissioning.md) | 中文

## Scope

本流程在 `/srv/dev` 之外准备已审阅的内部升级，仅在独立审阅确切提交后激活。它使用[不可变源安装器](deploy-house-source.zh.md)及 [LAN HTTPS 策略](deploy-house-https.zh.md)。不要向上游推送内部 fork。准备不提升 `current`、不重启 DSH，也不安装远程浏览器信任。

## 1. Prepare

选择位于组装提交的干净已登记工作树；禁止并发源写入者。使用 DSH 专用持久 Node 运行时，不替换全局 Node。构建前清理已删除软件包残留。

```sh
export PATH=/srv/deepseek-harness/toolchains/node-v24.21.0-linux-x64/bin:$PATH
pnpm run clean
bash scripts/deploy-dsh.sh --source "$SOURCE" --expected-sha "$SHA" --prepare-only
sudo bash "$RELEASE/scripts/commission-dsh.sh" prepare "$RELEASE" "$SHA"
```

将 `RELEASE` 设为输出的准备目录。其 `RELEASE_COMMIT` 必须等于 `SHA`。版本包含全部运行时产物和依赖，每个符号链接都限制在版本内。优先使用可用车队签发者；若没有，专用本地 CA 保存在 root 所有的 `/etc/dsh-edge/tls`。复用现有 TLS 文件，不轮换。服务器证书有效期一年；操作员负责到期前使用同一专用签发者续期。验证 IP SAN、证书链和至少 30 天剩余有效期。准备在安装软件包前屏蔽默认 nginx 单元，并使用不包含默认站点的专用入口单元。其他 nginx 服务不得处于活动状态。

不安装远程客户端信任。用 `ca.crt` 验证时 HTTPS 有效；目标浏览器需要按主机独立授权安装 CA。不得根据 `curl --cacert` 声称浏览器信任，不得用 `-k` 验收。

### 不重启 DSH，更新一个精确 VPN 客户端

将 `DSH_EXTRA_CLIENT_CIDRS` 设为以空白分隔的已批准客户端 CIDR，值须来自不含查询参数的入口访问日志或部署配置。每项必须使用规范单地址 `/32` 或 `/128` 语法且不含 IPv6 scope 标识符；子网、裸地址及格式错误的输入会失败，且不改变已保存的批准条目或生成的配置。准备先用 `nginx -t` 检查私有候选配置，再逐个原子替换已发布的配置文件；语法检查失败时现有文件保持不变。省略变量会复用 `/etc/dsh-edge/client-cidrs.conf`；设为空字符串则清除额外客户端。准备验证条目并保存到 root 所有、权限为 0600 的数据文件。现有 LAN 规则及服务器级 deny-all 仍适用于根路径、API 和 WebSocket；Host、Origin 及 cookie 要求不变。

仅修正入口时，将已审阅提交的投产工具及配套模板安装在 `/srv/dev` 之外；不要修改运行版本。`TOOL_SHA` 标识干净且已审阅的工具提交，`RELEASE` 和 `SHA` 标识不变的线上版本。不需要构建：

```sh
TOOL=/srv/deepseek-harness/tools/edge-$TOOL_SHA
sudo install -d -m 755 "$TOOL"
git archive "$TOOL_SHA" scripts/commission-dsh.sh scripts/deploy/nginx-dsh.conf.template | sudo tar -x -C "$TOOL"
sudo env DSH_EXTRA_CLIENT_CIDRS="$DSH_EXTRA_CLIENT_CIDRS" bash "$TOOL/scripts/commission-dsh.sh" prepare "$RELEASE" "$SHA"
```

准备复用证书、获取部署锁并运行 `nginx -t`，但不重新加载任何服务。独立审阅生成的配置并获得明确激活授权后，仅重新加载 `dsh-edge.service`，不重启 DSH。从批准的 VPN 客户端验证根路径／cookie 重新加载、API 及 WebSocket 访问、LAN 连续可用、相邻地址拒绝及不变的浏览器准入检查。服务重启使用生成的配置；后续省略变量的准备复用已保存条目。回退此入口修改时，以先前批准的条目（原来没有则为空）准备、验证并仅重新加载入口；不要使用数据迁移回滚操作。

## 2. Review and activate

审阅确切提交、准备的版本、生成的 nginx 配置、Node 运行时、兼容性／迁移／恢复证据及本回滚流程。记录独立回执；非空字符串是操作员证明，不是自动验证审阅。之后才能运行：

```sh
sudo bash "$RELEASE/scripts/commission-dsh.sh" activate "$RELEASE" "$SHA" "$RECEIPT"
```

命令获取部署锁、停止写入者，在 `/srv/deepseek-harness/rollback` 下私有保存所有数据及配套配置，解压并比较恢复测试，报告快照路径。通过受支持的 `dsh plugin` 操作固定 subscriptions 0.9.7 和 Hindsight 0.8.0，仅对 subscriptions 0.9.7 / DSH 0.2.1-alpha.1 设置豁免。此豁免接受精确记录的不兼容风险，不是全局绕过。凭据文件及持久浏览器签名密钥必须逐字节不变。运行时 drop-in 保留 `hindsight.conf`；最终配置层绑定回环并公布 HTTPS 地址。

若停止写入者后命令失败，服务可能保持停止：立即使用输出的一致快照回滚。不要用已改变数据启动旧运行时。若创建快照失败，则插件及版本尚未提升；检查错误后启动未改变的旧服务。保留最后已知可用版本及一致快照；投产过程中不要清理它们。

## 3. Validate

```sh
curl --noproxy '*' --cacert /etc/dsh-edge/tls/ca.crt -D /tmp/dsh-headers -o /dev/null https://192.168.100.200/
curl --noproxy '*' --cacert /etc/dsh-edge/tls/ca.crt -o /dev/null -w '%{http_code}\n' https://192.168.100.200/api
ss -ltn
systemctl is-active dsh.service dsh-edge.service
```

干净根路径必须签发签名值不变且带 Secure、HttpOnly、SameSite=Strict 的 cookie，并重定向 `./`；用该 cookie 重新加载须成功。未认证 API 返回 401。验证错误 Host、非空且不精确的 Origin、跨站 Fetch Metadata 及缺少精确 Origin 的升级在全部路径类别返回 403。直接 LAN 后端访问失败；唯一后端监听器为 `127.0.0.1:3080`，唯一入口监听器为 `192.168.100.200:443`。执行实际认证目录／状态请求，将全部 25 个身份、推理力度及默认值与恢复的旧运行时比较。除非独立授权，否则提供者生成及 OAuth 刷新仍未验证；不要仅为关闭此检查而花费模型调用。Hindsight diagnose/sync 可用真实后端而不生成模型响应，但若不能安全执行，注入及保留仍未验证。仅在保留脱敏验收证据后删除私有临时凭据副本。

## 4. Roll back

```sh
sudo bash "$RELEASE/scripts/commission-dsh.sh" rollback "$RELEASE" "$SHA" "$BACKUP"
```

回滚停止两个写入者、私有保留失败代数据，恢复一致数据／配置快照，并恢复旧版本指针及先前入口活动状态。宣布恢复前验证旧认证目录／状态。磁盘保留 v3 前代不能证明降级兼容；必须恢复配套数据和插件。

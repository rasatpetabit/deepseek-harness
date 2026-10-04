# 选择不可变的内部部署源

[English](deploy-house-source.md) | 中文

## Summary

为内部部署脚本选择一个已提交的工作树，不清理、stash 或构建其他检出。本流程准备源选择证据，不授权在线部署或重启。[协调决策](../../.agents/notes/proposed/architecture/2026-10-04-upstream-reconciliation.zh.md) 负责发布审阅及激活前提。

## Table of Contents

- [1. Select the reviewed source](#1-select-the-reviewed-source)
- [2. Verify scratch deployment behavior](#2-verify-scratch-deployment-behavior)
- [3. Hand off for activation](#3-hand-off-for-activation)

## 1. Select the reviewed source

向 [deploy-dsh.sh](../../scripts/deploy-dsh.sh) 同时提供 `--source <absolute-canonical-worktree>` 和 `--expected-sha <full-commit-SHA>`。源必须是 `/srv/dev/ai/deepseek-harness` 所属仓库中已登记的工作树；不接受其他克隆、符号链接别名、相对路径或子目录。预期 SHA 必须是确切已审阅提交，并与源 HEAD 相同。没有默认源，也不会回退到主检出。

部署完成前，所选工作树必须由部署独占。部署前索引、已跟踪文件和未被忽略的未跟踪文件必须干净，并在依赖安装、构建和输出暂存后保持干净。允许被忽略的生成输出及工作树本地依赖；指向版本目录之外的共享依赖链接会被最终隔离检查拒绝。不得为满足此要求而提交或 stash 其他检出中操作员所有的更改。

## 2. Verify scratch deployment behavior

在具备 Python、pytest、Git、Bash、rsync 和标准 Linux 工具的候选工作树中运行：

```sh
python3 -m pytest -q scripts/tests/test_deploy_source.py scripts/tests/test_deploy_overlay.py scripts/tests/test_deploy_provisioning.py scripts/tests/test_deploy_retention.py
bash -n scripts/deploy-dsh.sh
```

源测试运行真实脚本，仅将开发及生产根目录替换为临时路径，并使用受控的构建及服务命令。测试执行真实 Git 归档、输出复制、版本隔离及提升，不接触运行中的服务。要求测试及 shell 语法验证通过；这些检查不能证明已构建生产配置的兼容性。

## 3. Hand off for activation

独立确切制品审阅及全部投产前提满足后，发布操作员提供已审阅的规范源路径及完整提交 SHA。部署归档该固定 SHA，并仅覆盖所选工作树的输出。拒绝保持活动版本不变，也不重启服务；暂存失败移除未提升的版本。现有服务单元预配置、部署锁、服务健康验证和有界版本保留继续生效。

在 `/srv/dev` 之外保留一致的回滚数据、插件、设置、凭据、服务配置及边缘配置。脚本不修改生产数据目录，也不轮换凭据。提升之后的健康检查失败仍须执行现有操作员回滚流程；源验证不提供自动回滚或持久格式降级支持。

# AGENTS.md

## 项目说明

这是一个 Python 项目，用于使用交易数据和辅助的地理数据进行商户的地理聚类，从而形成商圈。

## 开发规范

- 使用 Python 3.7.1 至 3.7.x
- 代码保持简单，优先可读性
- 新增功能时同步更新 README
- 修改代码后运行测试或启动程序验证

## 交互偏好

- 先解释修改思路，再改代码
- 涉及删除文件、重构目录、安装依赖时先询问
- 回复使用中文

## Codex 特定配置

- 技能路径：/skills

## Plans

If the user specifically asks for a plan, present the summary of a plan and wait for user confirmation before proceeding with any code modifications. Do not do extensive research prior to presenting the plan. Succinctly summarize the task and the main requirements using informal but terse language (no need to use RFC 2119 modal verbs). If the task is clear from the prompt, ask the user if the plan looks good before proceeding. If there are aspects of the plan that require clarification or there are design tradeoffs, ask the user up to four questions. In cases where there is a clear choice between two or three options, phrase the question as multiple choice so the user can simply reply with A, B, C, etc. Do not modify any code until the user tells you that the plan is acceptable.

## Specs

If the user specifically asks for a spec, make sure that a spec exists for the task before proceeding with detailed planning. If no spec exists for the feature already, create a new one. Do not do extensive planning or research first. Instead, create a basic spec template with placeholders.

Do not create a spec if the user doesn't ask for one.

Use the following rules for specs:

- Specs should be written in markdown.
- Specs should be concise, including only critical information to capture intent, requirements, and high-level design decisions
- A `/specs` directory at the root of the project should contain specs for features. If this directory doesn't exist, create it. Do not place specs in the `/docs` directory unless explicitly told to do so.
- Within the `/specs` directory, subdirectories represent features or feature areas. Each directory contains one or more "md" files that contain the specification details. Each directory contains a single "spec.md" file.
- A complete spec contains: 
    1. Overview, which is a succinct description of the feature and the
   motivation behind it, 
   2. Requirements, which capture the intent and user journey, and 
   3. Design, which provides a high-level technical design considerations including architecture, standards, frameworks, and external dependencies. Do not include extra sections. Use bulleted lists in each section and be concise.
- Requirements should be listed as declarative statements that use RFC 2119 modal verbs (MUST, SHOULD, MAY) to express normative strength.
- For the initial spec, do not do extensive code exploration prior to generating the spec.
- When creating a new spec, questions for the user can be added to the bottom of the file in a section named "Open Questions".

After creating the spec, ask the user to review it. Proceed with implementing the spec only once the user confirms that it is complete and correct.

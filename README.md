# Agentic Platform Examples

Standalone example agents built for MongoDB Agentic Platform.

Each example agent lives in the `agents/` directory. Every agent is a
**self-contained application** — it has its own `agent.yaml`, dependencies,
tests, and deployment metadata, plus a `dev.yaml` for local-development settings
such as service ports. You can copy any single agent directory out of this repo
and run it on its own.

The repo also contains starter templates in `templates/`. Those directories are
clean scaffold sources intended for `agentic create`.

The repository is structured as a **uv workspace** with a root `agent.yaml`
monorepo manifest so agents can share workspace tooling and still be discovered
as separate deployable apps. The monorepo layout is a convenience, not a
requirement for any individual example.

The examples depend on Agentic Platform SDK packages via git-based `uv` sources.

## Agents

| Agent | Description |
|-------|-------------|
| [`agents/atlas-admin-agent/`](agents/atlas-admin-agent/) | MongoDB Atlas Administration API agent with human-in-the-loop approval |
| [`agents/code-reviewer-agent/`](agents/code-reviewer-agent/) | DeepAgent code-review orchestrator with specialist review skills |
| [`agents/data-analyst-agent/`](agents/data-analyst-agent/) | Data analyst demo with MongoDB query and chart artifacts |
| [`agents/insurance-agent/`](agents/insurance-agent/) | Insurance assistant built on the Runner SDK |
| [`agents/mta-alerts-agent/`](agents/mta-alerts-agent/) | MTA subway service alerts agent |
| [`agents/recruiting-assistant-agent/`](agents/recruiting-assistant-agent/) | Recruiting assistant with cross-session memory and outreach review |
| [`agents/remote-mcp/`](agents/remote-mcp/) | Remote MCP examples for GitHub, Glean, and Sentry |
| [`agents/simple-agent/`](agents/simple-agent/) | Web search assistant built on the Agentic Platform LangGraph SDK |
| [`agents/weather-agent/`](agents/weather-agent/) | Minimal LangGraph weather demo (single workspace) |

## Templates

| Template | Description |
|----------|-------------|
| [`templates/chatbot-client/`](templates/chatbot-client/) | Next.js chatbot UI that streams messages through the MongoDB Agentic Platform API |
| [`templates/hello-world-agent/`](templates/hello-world-agent/) | Minimal Daily inspiration assistant with date and memory tools |
| [`templates/insurance-agent/`](templates/insurance-agent/) | Clean starter version of the insurance assistant for the future `agentic create` flow |

## Getting Started

1. Install [uv](https://docs.astral.sh/uv/).
2. Run `uv sync` at the root to set up the workspace.
3. Use `uv run` inside any agent directory to run it.

To use an agent standalone (outside this monorepo), copy its directory, ensure
the `uv` sources in its `pyproject.toml` resolve correctly, and run `uv sync`
from within that directory.

## Interactive Demo Setup

To set up and run one of the existing demos, use the interactive wizard:

```bash
scripts/setup-agent
```

The wizard updates the `agentic` CLI on a best-effort basis, tries to pull the
latest version of this repo, lets you choose a demo from `agents/`, prompts for
an LLM provider key and optional provider base URL, and can reuse matching
values from existing `.env` files in other demos. For OpenAI-compatible setups,
it also prompts for `AZURE_OPENAI_API_VERSION` when the selected demo supports
that setting. It can enable memory by prompting for or reusing a
`VOYAGE_API_KEY`. It writes the selected demo's `.env`, updates the relevant
`agent.yaml` settings, and can start the demo with `agentic dev up`.

## Archive filtering (`.agenticignore`)

The root [`.agenticignore`](.agenticignore) controls which files `agentic build`
packs into the source archive it uploads. It uses standard `.gitignore` syntax
and `agentic init` seeds it with sensible defaults; edit it to fit your project.

Because this repo is a uv workspace, `agentic build` reads `.agenticignore` from
the **archive root — the workspace root** — not from an individual agent
directory, so a single file at the repo root applies to every agent here.
`.env` / `.env.*` files and the `.git` directory are always excluded and cannot
be re-included.

## Conventions

- The `agents/` directory contains one sub-directory per example agent.
- Each agent owns its own manifests, tests, and deployment metadata.
- The `templates/` directory contains starter scaffolds intended to be copied
  into new projects.
- The root `agent.yaml` lists every deployable agent workspace in the repo.
- The root `uv` workspace ties agents together for convenience but is not a
  hard requirement — agents are designed to work independently.
- Templates are starter scaffolds and are not part of the root `uv` workspace.
- Shared SDK code belongs in the Agentic Platform SDK; app-specific behavior
  belongs here.

___

# Quick Setup

More detailed development instructions can be found [here](https://feature-agentic-plat--docs-on-nextjs.netlify.app/docs/agentic-platform/).

The documentation preview is protected with a username and password. These credentials are not meant to be secret; they are intended only to prevent unauthorized access.

Username: `agentic-platform`
Password: `atlasap-preview`

## Install CLI

Download and install one of the CLI binaries from the releases page.

Depending on your OS, you will need to mark the binary as executable and put it on your path. 

For macOS or Linux (adjust paths to match your download):
```
chmod +x ./agentic_<version>_darwin_arm64
sudo mv ./agentic_<version>_darwin_arm64 /usr/local/bin/agentic 
```

For Windows
```
Rename-Item .\agentic_<version>_windows_x86_64.exe agentic.exe
New-Item -ItemType Directory -Force "$env:USERPROFILE\bin"
Move-Item .\agentic.exe "$env:USERPROFILE\bin\agentic.exe"
```
Then add `%USERPROFILE%\bin` to your user PATH if it is not already there:
```
[Environment]::SetEnvironmentVariable(
  "Path",
  $env:Path + ";$env:USERPROFILE\bin",
  "User"
)
```

To run the CLI, either set the env variable `export AP_SOURCE=public` or create an alias like `alias agentic='AP_SOURCE=public agentic'`

Then run `agentic` to get started. 

## Turn Off Sentry Reporting

Released CLI binaries may report unexpected local failures to Sentry. Sentry reporting is optional. If you prefer not to send Sentry reports, you can opt out for both the CLI and the generated local Docker Compose stack by setting `AGENTIC_SENTRY_ENABLED=0` in the same shell before running `agentic`.

For macOS or Linux:
```
export AP_SOURCE=public
export AGENTIC_SENTRY_ENABLED=0
agentic
```

Or include it in your alias:
```
alias agentic='AP_SOURCE=public AGENTIC_SENTRY_ENABLED=0 agentic'
```

For Windows PowerShell:
```
$env:AP_SOURCE = "public"
$env:AGENTIC_SENTRY_ENABLED = "0"
agentic
```

If you only want to opt out of Sentry reporting for generated Docker Compose services, add this line to your project `.env` file:
```
AGENTIC_SENTRY_ENABLED=0
```

## To get an Agent Running
Before trying to run an agent locally, make sure you have GitHub CLI, Docker Desktop, and Python 3.11+ installed.

Clone this repo and navigate to one of the examples. This repo includes:

- `agents/simple-agent-example` — web search assistant
- `agents/recruiting-assistant-agent` — recruiting assistant with local candidate data and memory seeds
- `agents/insurance-agent` — insurance assistant for policy and claims workflows

Then copy the example env file so you can update the secrets.
```
cd agents/simple-agent-example
cp env.example .env
```
Then update `.env` with your LLM base URL and API key.
```
<PROVIDER>_BASE_URL="https://chatgpt.com/"
<PROVIDER>_API_KEY="<your-api-key>"
```

Then run `agentic dev up` to get the local UI running.



If you have questions please reach out to your MongoDB contact.

___


### License Agreement

This License Agreement (the “**Agreement**”) establishes the terms on which MongoDB, Inc. (“**Company**”) grants a license to Company’s proprietary MongoDB Agentic Platform software solely in machine-readable, executable, object-code form and related documentation (the “**Software**”) to the licensee (“**You**” or “**Your**”) solely on the condition that You accept all of the terms in this Agreement. By clicking through any applicable acceptance screen, or otherwise accessing, installing, or using the Software, you are indicating your acceptance of this Agreement, and if you do not agree to the terms of this Agreement, you may not access, install, or use the Software. If You are an employee or agent of a company (the “**Customer**”), You hereby agree that You enter into this Agreement on behalf of the Customer and that You have the authority to bind the Customer to the terms and conditions of this Agreement. 

1. **LICENSE**.  During the Period (as defined below), subject to Your full and ongoing compliance with all terms and conditions of this Agreement, Company hereby grants You a limited, revocable, non-exclusive, non-transferable, non-sublicensable license to install and use the Software in your internal non-production environment and solely for the intended purpose of the Software. For clarity, you may only install and use the Software for local development, evaluation and testing purposes and not for any production or commercial use case. 

2. **RESTRICTIONS**.  You will not, and will not allow any third party to: (i) modify, alter, tamper with, repair, or otherwise create derivative works of the Software; (ii) sell, sublicense, rent, lease, distribute, market, or commercialize the Software; (iii) decompile, disassemble, translate, reverse engineer or otherwise attempt to derive source code from any portion of the Software, except and solely to the extent that the foregoing restriction is impermissible pursuant to applicable law or third party license; (iv) remove, alter or obscure any proprietary notices of Company, its licensors or suppliers included in the Software; or (v) publicly disseminate performance information about or analysis of the Software, including benchmarking or other test results; or (vi) use the Software to support products or services competitive to Company.  No third party may access, view or use the Software under this Agreement.

3. **NO FEES; OPERATING EXPENSES**.  Subject to the terms of this Agreement, You and Company agree that no license fees or other fees shall be payable under this Agreement in exchange for the rights granted and/or the use of the Software or other materials provided under this Agreement.

4. **CONFIDENTIALITY.** You will use the Software only in connection with the Agreement and protect the Company’s Software by using the same degree of care used to protect its own confidential information, but not less than a reasonable degree of care. You will limit disclosure of the Software to Your directors, officers, agents, representatives, employees and contractors who are bound to confidentiality obligations at least as protective as the confidentiality provisions in the Agreement and who have a need to know about the Software. You will not disclose information about the Software to any third party without the Company’s consent, except where required to comply with applicable law or a legal order or process, provided You will, if legally permitted, promptly notify the Company.

5. **FEEDBACK**.  If you choose to provide us with suggestions, ideas for improvement, recommendations or other feedback, we may use and modify your feedback without any restriction or payment.

6. **OWNERSHIP**.  The Software, and all worldwide intellectual property rights and proprietary rights to the Software, are the exclusive property of Company and its licensors.  Company and its licensors reserve all rights in and to the Software not expressly granted to You in this Agreement, and no other licenses or rights are granted by implication, estoppel or otherwise.

7. **TERM**.  This Agreement shall commence when you download or receive the Software and shall continue in force and effect until terminated by either party or, if earlier, the Software becomes generally available (“**Period**”).  Either party may terminate this Agreement, with or without cause, immediately upon written notice to the other party. Company may terminate this Agreement by posting a notice on its website. This Agreement will terminate immediately and without notice in the event that you breach any term or condition of this Agreement. Upon the expiration or any termination of this Agreement, the license and all rights granted to You under this Agreement will immediately terminate, and You shall promptly purge and destroy all copies of the Software in Your possession. Upon Company’s request, You will certify such deletion or destruction in writing. Provisions intended by their nature to survive termination of this Agreement survive termination.

8. **USAGE DATA.**  The Software may include features that provide us metadata about usage of the Software, and you hereby consent to our collection of such data, and to our storage, processing, and analysis of such data for our own internal business purposes. 

9. **WARRANTY DISCLAIMER**.  THE SOFTWARE IS PROVIDED TO YOU “AS IS” AND WITH NO REPRESENTATION OR WARRANTY OF ANY KIND.  EXCEPT TO THE EXTENT PROHIBITED BY LAW, COMPANY AND LICENSORS EXPRESSLY DISCLAIM ANY AND ALL WARRANTIES AND REPRESENTATIONS OF ANY KIND WITH REGARD TO THE SOFTWARE OR THIS AGREEMENT, INCLUDING ANY WARRANTY OF NON-INFRINGEMENT, TITLE, FITNESS FOR A PARTICULAR PURPOSE, FUNCTIONALITY OR MERCHANTABILITY, WHETHER EXPRESS, IMPLIED OR STATUTORY. 

10. **LIMITATION OF REMEDIES**.  IN NO EVENT SHALL COMPANY BE LIABLE FOR ANY INCIDENTAL, INDIRECT, SPECIAL, CONSEQUENTIAL OR PUNITIVE DAMAGES IN CONNECTION WITH THIS AGREEMENT, REGARDLESS OF THE NATURE OF THE CLAIM OR THEORY OF LIABILITY, INCLUDING, WITHOUT LIMITATION, LOST PROFITS, COSTS OF DELAY, ANY FAILURE OF DELIVERY, BUSINESS INTERRUPTION, COSTS OF LOST OR DAMAGED DATA OR DOCUMENTATION OR LIABILITIES TO THIRD PARTIES ARISING FROM ANY SOURCE, REGARDLESS OF WHETHER THE COMPANY HAS BEEN NOTIFIED OF THE POSSIBILITY OF SUCH DAMAGES. WITHOUT LIMITING THE FOREGOING, COMPANY CUMULATIVE LIABILITY FOR ALL CLAIMS ARISING FROM OR RELATING TO THIS AGREEMENT, INCLUDING, WITHOUT LIMITATION, ANY CAUSE OF ACTION SOUNDING IN CONTRACT, TORT, OR STRICT LIABILITY, SHALL NOT EXCEED ONE HUNDRED DOLLARS (U.S. $100.00).  THE FOREGOING LIMITATIONS WILL APPLY NOTWITHSTANDING THE FAILURE OF ESSENTIAL PURPOSE OF ANY LIMITED REMEDY PROVIDED HEREIN.

11. **ESSENTIAL BASIS OF AGREEMENT**.  The Parties acknowledge and agree that the disclaimers, exclusions and limitations of liability set forth in Section 11 form an essential basis of this Agreement, and that, absent any of such disclaimers, exclusions or limitations of liability, the terms of this Agreement, including, without limitation, the economic terms, would be substantially different.

12. **GENERAL**.  This Agreement shall be governed by and interpreted in accordance with the laws of the State of New York, without regard to conflicts of law principles thereof or to the United Nations Convention on the International Sale of Goods.  For purposes of all claims brought under this agreement, each of the parties hereby irrevocably submits to the exclusive jurisdiction of the state and federal courts located within the State of New York. Company may assign this Agreement, in whole or in part, at any time with or without notice to You.  You may not assign this Agreement, or any part of it, to any other party.  Any attempt by You to do so is null and void.  If any provision of this Agreement is held to be unenforceable, that provision will be enforced to the extent permissible by law and the remaining provisions will remain in full force and effect.  No waiver under this Agreement shall be valid or binding unless set forth in writing and duly executed by the party against whom enforcement of such waiver is sought.  Any such waiver shall constitute a waiver only with respect to the specific matter described therein and shall in no way impair the rights of the party granting such waiver in any other respect or at any other time.  Any delay or forbearance by either party in exercising any right hereunder shall not be deemed a waiver of that right.  This Agreement is the complete and exclusive statement of the agreement between us and supersedes any proposal or prior agreement, oral or written, and any other communications between You and Company in relation to the subject matter of this Agreement.

If You have any questions regarding this Agreement or the Software, please direct all correspondence to:  legal@mongodb.com.

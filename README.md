# GPUDeploy Guard

Kubernetes GPU/LLM workload 배포 전 설정과 GPU 준비 상태를 검사하기 위한 Python CLI 프로젝트입니다.
현재 구현 범위는 **Static YAML Validation, 조회 전용 Cluster Preflight, 단일 Pod scheduling eligibility와 GPU capacity feasibility**입니다.

## Problem

Kubernetes API가 Deployment object를 받아들였다는 사실만으로 GPU Pod가 실행 가능한 것은 아닙니다.
GPU resource 누락, Ready Node 부족, selector/affinity/taint 불일치 또는 단일 Node capacity 부족은
배포 후 Pod를 Pending 상태로 남길 수 있습니다. GPUDeploy Guard는 apply 전에 이 조건을
결정론적 JSON 결과와 exit code로 보여 주어 원인을 먼저 확인할 수 있게 합니다.

## What it checks

| 명령 | 검사 범위 |
|---|---|
| `check FILE` | GPU limit, CPU/Memory request·limit 형식, Startup/Readiness/Liveness Probe |
| `cluster-check` | kubectl, current context, API 연결, Ready Node, `nvidia.com/gpu` allocatable, Device Plugin Pod 존재 |
| `workload-check FILE` | Static 검사 + Ready/nodeName/nodeSelector/required nodeAffinity/cordon/taint 조건 + 단일 Node GPU fit |

이 도구는 scheduler 전체를 재현하지 않습니다. 지원한 조건에 대한 preflight이며,
PASS도 실제 scheduling 성공을 보장하지 않습니다.

## Verified evidence

| 구분 | observed 결과 |
|---|---|
| 자동 검증 | 로컬 pytest **509 passed**; commit `aabf50c`의 [CI Validation 성공](https://github.com/eunsu7997/gpu-deploy-guard/actions/runs/36882281894) |
| Host/Docker | RTX 4060 인식, Docker GPU access, CUDA vector addition `Test PASSED` |
| 현재 kind 환경 | Kubernetes API 연결 및 Node Ready VERIFIED; CPU-only Node이며 `nvidia.com/gpu` capacity/allocatable 미노출 |
| Preflight | GPU 2개 요청에 `allocatable=0, required=2`를 감지하고 exit 1 |
| 실제 scheduler 비교 | Deployment object 생성 후 Pod Pending; scheduler가 `Insufficient nvidia.com/gpu`를 보고하여 preflight와 **MATCH** |

**NOT VERIFIED:** 실제 GPU Kubernetes Node의 `nvidia.com/gpu` 광고,
실제 GPU Node에서 NVIDIA Device Plugin runtime 정상 동작, Kubernetes GPU workload 실제 배치,
GPU Pod 또는 vLLM Pod 실행.

## Quick start

Python 3.10 이상과 프로젝트 루트를 기준으로 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m gpu_guard check examples/good/probes_complete.yaml
.\.venv\Scripts\python.exe -m gpu_guard cluster-check
.\.venv\Scripts\python.exe -m gpu_guard workload-check examples/good/workload_two_gpu.yaml
```

현재 저장된 kind evidence에서는 `cluster-check`가 GPU 관련 WARN과 exit 0,
`workload-check`가 GPU capacity 부족 FAIL과 exit 1을 반환했습니다.
일반적인 exit code 계약은 0=PASS/WARN, 1=rule FAIL, 2=input/CLI error입니다.

## Architecture / Repository map

```text
pyproject.toml                  패키징, 의존성, CLI 진입점, pytest 설정
src/gpu_guard/__init__.py        패키지
src/gpu_guard/__main__.py        python -m gpu_guard 진입점
src/gpu_guard/cli.py             check 명령과 인자 처리
src/gpu_guard/validators.py      GPU limit deterministic rule
src/gpu_guard/resource_validators.py  독립적인 CPU/Memory 검사
src/gpu_guard/probe_validators.py     독립적인 Startup/Readiness/Liveness 검사
src/gpu_guard/kubectl.py             조회 전용 Runner·명령 결과·timeout 처리
src/gpu_guard/cluster_checks.py      Live Cluster Preflight 판정
src/gpu_guard/feasibility.py         Static 결과·node snapshot 기반 GPU capacity 비교
src/gpu_guard/node_snapshot.py       name/Ready/labels/cordon/taints/GPU 정규화
src/gpu_guard/eligibility.py         scheduling constraint 후보 필터 및 GPU 판정 연결
src/gpu_guard/affinity.py            required nodeAffinity 파싱·expression 검증·label 평가
.github/workflows/ci.yml             PR/main push 자동 테스트·Static CLI·evidence 저장
examples/good/gpu_limit.yaml     GPU limit 1 (PASS)
examples/bad/missing_gpu.yaml    GPU limit 누락 (FAIL)
examples/bad/zero_gpu.yaml       GPU limit 0 (FAIL)
examples/good/resources_complete.yaml   GPU·CPU·Memory 정상 예시
examples/bad/missing_cpu_limit.yaml     CPU limit 누락 (CPU WARN)
examples/bad/missing_memory_request.yaml Memory request 누락 (Memory WARN)
examples/bad/missing_resources.yaml     resources 누락 (GPU FAIL, CPU·Memory WARN)
examples/bad/invalid_resource_value.yaml 잘못된 CPU·Memory 값 (FAIL)
examples/good/probes_complete.yaml      세 Probe 정상 (PASS)
examples/good/workload_two_gpu.yaml     단일 컨테이너 GPU 2 요청
examples/good/workload_multi_container.yaml 컨테이너 GPU 1+1, Pod 합계 2
examples/good/node_selector_match.yaml GPU 모델·지역 selector 예시
examples/good/toleration_match.yaml    GPU NoSchedule Equal toleration 예시
examples/bad/node_selector_mismatch.yaml selector 불일치 예시
examples/bad/missing_toleration.yaml   taint toleration 누락 예시
examples/bad/fixed_node_missing.yaml   존재하지 않는 nodeName 예시
examples/good/affinity_in_match.yaml   In affinity 예시
examples/good/affinity_or_terms.yaml   Term 간 OR 예시
examples/bad/affinity_in_mismatch.yaml affinity label 불일치 예시
examples/bad/affinity_numeric_invalid.yaml 잘못된 Gt threshold 예시
examples/bad/affinity_no_eligible_gpu_node.yaml affinity 후보 GPU 부족 예시
examples/bad/missing_startup_probe.yaml Startup 누락 (WARN)
examples/bad/missing_readiness_probe.yaml Readiness 누락 (WARN)
examples/bad/missing_liveness_probe.yaml Liveness 누락 (WARN)
examples/bad/invalid_probe_handler.yaml handler 누락·빈 값 (FAIL)
tests/test_cli.py               CLI 실행 테스트
tests/test_validators.py        규칙 및 evidence 테스트
tests/test_resource_validators.py  CPU/Memory 규칙·독립성 테스트
tests/test_probe_validators.py     Probe 규칙·evidence·독립성 테스트
tests/test_cluster_checks.py       Fake 기반 클러스터 검사 테스트
tests/test_kubectl.py              Mock 기반 명령·timeout·조회 제한 테스트
tests/test_cluster_cli.py          Fake 기반 새 CLI·종료 코드·Static 분리 테스트
tests/test_feasibility.py          단일 노드 GPU capacity·NotReady 제외 테스트
tests/test_workload_cli.py         결합 CLI·snapshot 재사용·오류·회귀 테스트
tests/test_eligibility.py          scheduling 필터·taint/toleration·GPU 일관성 테스트
tests/test_affinity.py             required affinity syntax·operator·OR/AND·결합 테스트
```

## 설치 및 실행

Python 3.10 이상 환경에서 프로젝트 루트를 작업 디렉터리로 사용합니다.

```sh
python -m venv .venv
```

Windows PowerShell에서는 `.\.venv\Scripts\Activate.ps1`, POSIX에서는
`source .venv/bin/activate`로 가상 환경을 활성화합니다.

```sh
python -m pip install -e ".[dev]"
python -m gpu_guard --help
python -m gpu_guard check examples/bad/missing_gpu.yaml
python -m gpu_guard check examples/good/resources_complete.yaml
python -m gpu_guard check examples/bad/missing_memory_request.yaml
python -m gpu_guard check examples/good/probes_complete.yaml
python -m gpu_guard check examples/bad/invalid_probe_handler.yaml
python -m gpu_guard cluster-check
python -m gpu_guard workload-check examples/good/workload_two_gpu.yaml
python -m gpu_guard workload-check examples/good/node_selector_match.yaml
python -m gpu_guard workload-check examples/good/affinity_in_match.yaml
python -m pytest
```

설치 후 `gpu-guard` 명령도 같은 CLI를 호출합니다.
`check`는 PyYAML의 `safe_load`로 단일 Deployment YAML을 읽고,
`spec.template.spec.containers`의 각 컨테이너에 대해
`resources.limits['nvidia.com/gpu']`를 검사합니다.
유한한 숫자 값이 1 이상이면 PASS, 누락·0·1 미만·잘못된 값이면 FAIL입니다.
숫자로 변환 가능한 문자열도 허용하고 boolean은 거부합니다.
이 규칙은 GPU 개수의 정수 여부를 포함한 Kubernetes 전체 스키마를 검증하지 않습니다.
모든 일반 컨테이너가 검사 대상이며 initContainers는 아직 검사하지 않습니다.

CPU와 Memory는 각각 `cpu_resources`, `memory_resources` check로 검사하며
기존 `gpu_resource_limit` 판정과 독립적입니다.
각 컨테이너의 `resources.requests.cpu/memory`, `resources.limits.cpu/memory`에 대해:

| 상태 | 규칙 |
|---|---|
| PASS | request와 limit이 모두 존재하고 값 형식이 유효함 |
| WARN | request만 존재, limit만 존재, 또는 둘 다 누락 |
| FAIL | 존재하는 값이 빈 값·null·잘못된 형식이거나 부모 resources/requests/limits가 mapping이 아님 |

값 형식 검사는 0 이상의 Kubernetes quantity 구문을 지원합니다.
예: `500m`, `2`, `0.5`, `128Mi`, `8Gi`, `1G`, `1e3`.
음수, boolean, 배열/객체, 공백·빈 문자열, `NaN`, `Infinity`, 잘못된 suffix는 FAIL입니다.
필드 누락과 명시적 null을 구분하며, 잘못된 값이 있으면 누락 WARN보다 FAIL이 우선합니다.
quantity 문법은 [Kubernetes Quantity 소스](https://github.com/kubernetes/apimachinery/blob/master/pkg/api/resource/quantity.go)를 참고했습니다.
request 누락은 YAML에 명시되지 않았다는 WARN이며 클러스터의 자동 기본값 처리는 추론하지 않습니다.
request와 limit의 크기 비교, CPU 최소 정밀도, quantity 상한·정규화 및 전체 Kubernetes 스키마 검사는 아직 수행하지 않습니다.

## Health probe 검사

일반 컨테이너마다 `startup_probe`, `readiness_probe`, `liveness_probe` check를 각각 출력합니다.
GPU·CPU·Memory 판정은 Probe 판정과 독립적입니다.

| 상태 | 규칙 |
|---|---|
| WARN | 해당 Probe 필드가 누락됨 |
| PASS | Probe가 mapping이고 지원하는 handler 하나의 검사 대상 필드가 유효함 |
| FAIL | Probe가 null·잘못된 구조이거나 handler가 없음·여러 개임·빈 값 또는 비정상 구조임 |

지원하는 handler는 `httpGet`, `tcpSocket`, `exec`입니다.

- `httpGet`·`tcpSocket`: port는 정수 1~65535 또는 유효한 named port여야 합니다. boolean·숫자 문자열은 거부합니다.
- `httpGet`: path는 생략할 수 있으며, 명시하면 `/`로 시작하는 비어 있지 않은 문자열이어야 합니다.
  scheme은 명시 시 `HTTP` 또는 `HTTPS`, host는 명시 시 비어 있지 않은 문자열이어야 합니다.
  httpHeaders는 명시 시 비어 있지 않은 name/value 문자열을 가진 mapping의 비어 있지 않은 배열이어야 합니다.
- `tcpSocket`: host는 명시 시 비어 있지 않은 문자열이어야 합니다.
- `exec`: command는 비어 있지 않은 문자열로 구성된 비어 있지 않은 배열이어야 합니다.

evidence에는 컨테이너 위치·이름, 실제 Probe 값과 잘못된 필드가 포함됩니다.
명시적 빈 optional 값도 이 도구의 규칙에서는 FAIL입니다.
gRPC handler는 이번 범위에서 지원하지 않으며, 존재하면 FAIL과 미지원 evidence를 출력합니다.
Probe의 timeout/period/threshold, 알 수 없는 필드 및 전체 API 스키마는 아직 검증하지 않습니다.
named port가 실제 containers[].ports에 정의되어 있는지 확인하거나 endpoint·command를 실행하지 않습니다.
Probe 동작 및 HTTP 기본 path는 [Kubernetes Probe 문서](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)를 참고했습니다.

출력은 `check`, `status`, `evidence`, `recommendation`을 가진 JSON 배열입니다.
evidence에는 컨테이너 경로·이름 및 실제 값 또는 `missing`이 포함됩니다.
FAIL이 없으면 PASS/WARN 조합은 종료 코드 **0**, 하나라도 규칙 FAIL이면 **1**입니다.
따라서 `missing_memory_request.yaml`은 Memory WARN을 출력하고 종료 코드 0을 반환하며,
`missing_resources.yaml`은 독립적인 GPU FAIL 때문에 종료 코드 1을 반환합니다.
파일 읽기·YAML 파싱·지원하지 않는 입력 구조 오류는 `manifest_input` FAIL과
실제 오류 evidence를 출력하고 종료 코드 **2**를 반환합니다.
도움말은 종료 코드 0, 잘못된 CLI 인자는 argparse 오류와 종료 코드 2를 반환합니다.
예시 이미지 주소는 placeholder이며 실제 배포용 이미지가 아닙니다.

## Live Cluster Preflight

`python -m gpu_guard cluster-check`는 Static YAML Validation과 별도로 실행하며
Kubernetes 리소스를 생성·수정·삭제하지 않습니다. `check FILE`은 kubectl을 호출하지 않습니다.

| check | 판정 |
|---|---|
| kubectl_availability | PATH에 실행 파일이 있으면 PASS, 없으면 FAIL |
| current_context | 실제 context 이름을 얻으면 PASS, 빈 값·조회 실패·timeout이면 FAIL |
| api_connection | nodes 조회 성공이면 PASS, 실패·timeout이면 FAIL |
| node_ready | 모두 Ready=True면 PASS, 일부만 Ready면 WARN, Ready가 0이면 FAIL |
| gpu_allocatable | 노드의 nvidia.com/gpu allocatable 합계 ≥ 1이면 PASS, 0·키 누락이면 WARN |
| nvidia_device_plugin | kube-system의 Pod 이름 또는 name/app/app.kubernetes.io/name label로 확인되면 PASS, 미확인이면 WARN, 조회·파싱 실패면 FAIL |

모든 결과는 `check`, `status`, `evidence`, `recommendation` JSON 필드를 제공합니다.
FAIL이 하나라도 있으면 종료 코드 1, PASS/WARN만 있으면 0입니다.
kubectl/context 실패 시 후속 검사는 `not executed` evidence와 FAIL로 표시합니다.
nodes 조회 실패 시 Ready·GPU 검사는 수행하지 않으며, Pod 조회는 별도로 시도합니다.
조회가 성공해도 JSON이 손상되었거나 필요한 구조가 비정상이면 해당 데이터 검사는 FAIL입니다.
GPU 키 누락·0은 WARN이며, 존재하는 GPU 값이 비정상적인 정수 형식이면 FAIL입니다.

명령 실행은 `KubectlRunner`, 검사 로직은 `cluster_checks`로 분리하고
`CommandRunner` 인터페이스로 Fake Runner를 주입할 수 있습니다.
Runner가 허용하는 조회는 다음 세 종류뿐입니다.

```text
kubectl config current-context
kubectl get nodes -o json --context=<읽은 context> --request-timeout=10s
kubectl get pods -n kube-system -o json --context=<읽은 context> --request-timeout=10s
```

명령은 shell 없이 실행하며 호출마다 subprocess timeout 10초를 적용합니다.
API 조회는 읽은 context를 명시하여 중간에 current-context가 바뀌어도 대상을 유지합니다.
실패 evidence는 실제 stderr를 최대 1200자로 제한하고 Python traceback은 노출하지 않습니다.
Device Plugin 식별 기준은 [NVIDIA 공식 manifest](https://github.com/NVIDIA/k8s-device-plugin/blob/main/deployments/static/nvidia-device-plugin.yml)를 참고했습니다.

### 검증 범위

2026-10-01 작업 전 전체 271개, 구현 후 전체 343개 pytest 테스트가 통과했습니다.
테스트는 실제 cluster에 의존하지 않고 Fake command result와 Mock subprocess를 사용합니다.
해당 구현 단계 당시 PC에서 cluster-check CLI를 실제 실행했으며 `kubectl executable not found`와 종료 코드 1을 확인했습니다.
**해당 구현 단계 당시 환경에서는 Live Cluster 실행을 검증하지 못함.** kubectl 설치나 cluster 설정은 수행하지 않았습니다.
API 연결·Node Ready·GPU allocatable·Device Plugin 성공 판정은 fake/mock으로만 검증했습니다.

allocatable은 이미 사용 중인 GPU를 차감한 잔여량이 아닙니다.
Device Plugin 검사는 Pod 존재 확인이며 Running/Ready·노드별 배치·드라이버 동작을 검증하지 않습니다.
kube-system 외 namespace, 사용자 정의 plugin 이름/label, MIG resource key는 아직 지원하지 않습니다.

## Workload GPU capacity feasibility

`python -m gpu_guard workload-check FILE`은 다음 순서로 실행합니다.

1. 기존 GPU·CPU·Memory·Probe Static Validation
2. 기존 조회 전용 Cluster Preflight와 node snapshot 수집
3. `node_eligibility` 후보 필터와 후보에 한정한 `gpu_feasibility` 판정

`ClusterSnapshot`은 Preflight 결과, 파싱한 node 목록 또는 실제 조회 오류를 보존합니다.
Preflight와 Feasibility는 **동일한 nodes 조회 결과**를 사용하며 nodes를 두 번 조회하지 않습니다.
Feasibility Evaluator는 subprocess나 파일 I/O를 수행하지 않습니다.
기존 GPU 숫자 해석과 Node Ready/allocatable 파싱은 공통 함수를 사용합니다.
기존 `check FILE`과 `cluster-check`의 출력 및 종료 코드 계약은 유지됩니다.

일반 컨테이너 각각의 `resources.limits['nvidia.com/gpu']`를 읽고 Pod 내 합계를 구합니다.
이는 limits 기반 GPU request 해석입니다. 관련 동작은 [Kubernetes GPU 문서](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/)를 참고했습니다.
누락된 GPU limit은 0으로 해석하고 컨테이너별 evidence에 표시합니다.
기존 Static Validator의 누락/잘못된 값 FAIL은 제거하거나 PASS로 바꾸지 않습니다.
명시적 GPU 0도 기존 Static FAIL을 유지하며, 잘못된 값의 evidence를 Feasibility FAIL에서 재사용합니다.
기존 Static 검사에서 허용하는 소수 GPU 값도 이 단계에서는 공유 미지원으로 Feasibility FAIL입니다.

| gpu_feasibility 판정 | 조건 |
|---|---|
| PASS | node snapshot을 얻었고 Pod GPU request가 0: GPU scheduling not required |
| PASS | 하나 이상의 Ready Node가 Pod GPU request 합계 이상의 allocatable GPU를 제공 |
| FAIL | 어떤 단일 Ready Node도 request를 충족하지 못함, Ready Node 없음, 또는 요청 GPU를 제공하는 Ready Node 없음 |
| FAIL | 잘못된 workload GPU 값, 지원하지 않는 소수 GPU 요청, 손상된 node 정보, 또는 node snapshot 수집 실패 |

**Node A=1 + Node B=1 / Pod request=2는 FAIL**입니다. Cluster 전체 합계로 PASS하지 않습니다.
**NotReady Node GPU=4 + Ready Node GPU=1 / request=2도 FAIL**입니다.
request=1이면 Ready Node GPU=1로 capacity 관점에서 PASS입니다.
GPU 없는 Ready cluster의 `cluster-check.gpu_allocatable`은 계속 WARN이며,
GPU workload의 `gpu_feasibility`는 FAIL입니다.

Feasibility evidence는 JSON object이며 workload 합계, 컨테이너별 요청,
Ready 노드별 GPU, 제외된 NotReady 노드별 GPU, 단일 Ready Node 최대 GPU와 후보 노드를 포함합니다.
조회가 불가능하면 `live feasibility not executed` 및 실제 오류를 표시합니다.
GPU request=0이어도 snapshot 조회 실패는 실행 불가 FAIL입니다.
Feasibility PASS는 독립적인 Static/Preflight FAIL을 덮어쓰지 않습니다.
전체 출력 중 하나라도 FAIL이면 종료 코드 1, 파일·YAML 구조 입력 오류는 기존처럼 2입니다.

### 지원 범위와 정확성 제한

**Node별 allocatable GPU capacity와 workload GPU request를 비교**합니다.
이미 실행 중인 Pod의 GPU 사용량을 차감하지 않으므로 allocatable은 현재 완전히 비어 있는 GPU 수가 아닙니다.
PASS는 GPU capacity 조건 충족이며 실제 scheduling 성공을 보장하지 않습니다.

단일 Pod template의 일반 containers와 일반 NVIDIA `nvidia.com/gpu`만 지원합니다.
Deployment replicas를 곱하지 않으며 여러 replica의 동시 배치를 검증하지 않습니다.
initContainers, preferred nodeAffinity·Pod affinity, CPU/Memory 잔여 capacity 및 quota는 Feasibility에 반영하지 않습니다.
Multi-node distributed inference, MPI, Ray cluster, node 간 tensor parallel,
MIG, Dynamic Resource Allocation, GPU sharing/time-slicing, autoscaling은 지원하지 않습니다.

### 6단계 검증 기록

작업 전 `git status --short`로 Git 저장소가 아님을 확인하여 commit을 생성하지 않았습니다.
작업 전 전체 343개 pytest 테스트가 통과했습니다.
6단계 구현 후 전체 385개 테스트가 통과했습니다.
단일 노드 조건, NotReady 제외, multi-container 합계, malformed 값·JSON,
kubectl 부재/API 실패, nodes 조회 1회 재사용은 Fake/Mock으로 검증했습니다.
해당 구현 단계 당시 PC에서 `workload-check examples/good/workload_two_gpu.yaml`을 실제 실행했습니다.
Static checks는 PASS, kubectl 부재로 Live checks와 Feasibility는 FAIL, 종료 코드는 1이었습니다.
**해당 구현 단계 당시 환경에서는 Live Cluster 실행을 검증하지 못함.** kubectl/Kubernetes 설치·설정은 수행하지 않았습니다.

## Node eligibility (7단계)

`workload-check FILE` 결과에 독립적인 `node_eligibility` check를 추가합니다.
기존 `check FILE`과 `cluster-check`의 검사/출력/종료 코드는 유지됩니다.
workload-check는 Static → Preflight → node_eligibility → gpu_feasibility 순서로 출력하며
nodes 조회는 계속 1회입니다. GPU request 추출 결과를 재사용하고 평가 함수는 subprocess를 호출하지 않습니다.

`ClusterSnapshot.scheduling_nodes`는 기존 raw nodes를 다음 정보로 정규화합니다:
`name`, `ready`, `labels`, `unschedulable`, `taints`, `allocatable_gpu`.
노드 정보나 workload constraint 구조가 잘못되면 실제 값과 함께 FAIL을 반환합니다.

지원하는 후보 필터 순서는 다음과 같습니다.

1. Ready=True
2. nodeName: 지정된 정확한 이름만 허용; 존재하지 않으면 FAIL
3. nodeSelector: 모든 key/value가 node label과 정확히 일치해야 함
4. required nodeAffinity: label expression 규칙 충족 (8단계 추가)
5. spec.unschedulable=true인 노드는 제외
6. NoSchedule/NoExecute taint 각각에 matching toleration이 있어야 함
7. 단일 후보 노드 allocatable GPU가 Pod GPU request 합계를 충족해야 함

nodeName은 selector보다 먼저 적용되는 강한 제한이며 다른 노드로 fallback하지 않습니다.
이 도구는 명시적 nodeName에도 selector·Ready·cordon·taint 정책을 적용하는 보수적인 preflight입니다.
nodeName 직접 binding에 따른 scheduler 우회 동작 자체를 재현하지 않습니다.
GPU 요청이 없어도 node_eligibility는 최소 한 후보 노드를 요구합니다.
eligible node가 있으면 PASS, 없으면 FAIL입니다. gpu_feasibility도 GPU를 요청하는 경우
constraint를 통과한 단일 노드만 사용하므로 selector mismatch/cordon 노드의 GPU로 PASS하지 않습니다.

Taint/toleration 정책:

- Equal: key/value 일치 및 effect 일치. operator 생략은 Equal입니다.
- Exists: key/effect 일치, value 불필요. 빈 key와 Exists는 모든 key를 허용합니다.
- effect 생략 또는 빈 문자열: 모든 effect를 허용합니다.
- 여러 NoSchedule/NoExecute taint가 있으면 각각 toleration이 필요합니다.
- PreferNoSchedule: 비차단 정보로 evidence에 기록하며, 단독으로 PASS를 WARN/FAIL로 바꾸지 않습니다.
- tolerationSeconds 형식은 검사하지만 시간 경과에 따른 NoExecute eviction은 시뮬레이션하지 않습니다.

effect 생략과 Exists 의미는 [Kubernetes Taints and Tolerations 문서](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/)를 참고했습니다.
Evidence에는 workload selector/nodeName/tolerations/GPU request, 정규화한 node 정보,
node별 제외 이유·비차단 정보, constraint 후보 및 최종 eligible node 이름을 기록합니다.
gpu_feasibility에는 eligible 후보별 GPU capacity와 후보 노드 최대 capacity를 추가합니다.

미지원: preferred nodeAffinity, matchFields, podAffinity, podAntiAffinity, topologySpreadConstraints,
ResourceQuota, PodDisruptionBudget, PriorityClass/preemption, multi-replica scheduling,
actual free GPU usage, MIG/DRA/GPU sharing 및 전체 Kubernetes API 스키마 검증.
PASS는 이번 단계에서 지원한 조건 충족이며 실제 배치를 보장하지 않습니다.
예시 YAML의 good/bad 결과는 조회한 cluster snapshot에 따라 달라집니다.
특히 missing_toleration.yaml은 GPU NoSchedule taint가 있는 Fake node를 기준으로 FAIL 테스트합니다.

### 7단계 검증 기록

작업 전 전체 385개 pytest 테스트가 통과했습니다.
구현 후 전체 435개 테스트가 통과했습니다. 기존 검사 회귀와 새 scheduling 사례는 Fake/Mock으로 검증했습니다.
workload-check 결과가 한 check 늘어나므로 기존 테스트의 결과 개수 assertion만 13→14로 갱신했습니다.
selector mismatch GPU 4 + selector match GPU 1 / request 2는 eligibility와 GPU feasibility 모두 FAIL입니다.
selector match GPU 2 + selector mismatch GPU 4 / request 2는 모두 PASS입니다.
NotReady·cordon·NoSchedule·NoExecute·Equal·Exists·effect 생략·PreferNoSchedule 정책을 테스트했습니다.
해당 구현 단계 당시 PC에서 `workload-check examples/good/node_selector_match.yaml`을 실제 실행했고
kubectl 부재로 Live/Eligibility/Feasibility FAIL과 종료 코드 1을 확인했습니다.
**해당 구현 단계 당시 환경에서는 Live 성공 경로를 검증하지 못함.** kubectl/Kubernetes 설치·설정은 수행하지 않았습니다.

## Required nodeAffinity (8단계)

지원 대상은 `spec.template.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution`입니다.
`affinity.py`가 workload rule을 한 번 파싱·검증하고 Node label을 평가합니다.
`eligibility.py`는 Ready/nodeName/nodeSelector/affinity/cordon/taint/GPU 조건을 조합합니다.
두 모듈은 subprocess를 호출하지 않습니다. 기존 명령과 결과 check 개수는 그대로 유지합니다.

- `nodeSelectorTerms` 간 관계는 **OR**입니다.
- 한 Term의 `matchExpressions` 간 관계는 **AND**입니다.
- 모든 Term을 사전 검증하므로 유효한 OR branch가 있어도 다른 branch의 malformed rule은 FAIL입니다.
- 빈 Term은 모든 노드를 매칭하는 것이 아니라 **아무 노드도 매칭하지 않습니다**.

| operator | 매칭 의미 | values |
|---|---|---|
| In | key 존재 및 label 값이 목록에 포함 | 비어 있지 않은 문자열 배열 |
| NotIn | key가 없거나 label 값이 목록에 미포함 | 비어 있지 않은 문자열 배열 |
| Exists | key 존재 | 생략 또는 빈 배열 |
| DoesNotExist | key 미존재 | 생략 또는 빈 배열 |
| Gt | label 정수 > threshold | 정확히 한 정수 문자열 |
| Lt | label 정수 < threshold | 정확히 한 정수 문자열 |

NotIn의 key 미존재 매칭과 values 조건은 [Kubernetes 공식 selector 구현](https://github.com/kubernetes/apimachinery/blob/master/pkg/labels/selector.go)을 확인했습니다.
Gt/Lt는 ASCII 십진수 signed 64-bit 정수로 해석합니다.
Node label이 없거나 정수로 파싱되지 않으면 해당 Node는 mismatch이며 실제 값과 이유를 기록합니다.
affinity threshold가 정수가 아니거나 범위를 벗어나면 workload rule 자체가 invalid FAIL입니다.
In/NotIn의 빈 문자열 label 값은 허용되지만 values 배열 자체가 비어 있으면 FAIL입니다.

nodeSelector와 required nodeAffinity는 둘 다 충족해야 합니다.
nodeName으로 제한된 노드가 affinity mismatch이면 다른 노드로 fallback하지 않습니다.
affinity mismatch 노드의 GPU는 최종 후보 capacity에 포함되지 않습니다.
Evidence에는 원본 required rule, 노드별 평가한 Term과 mismatch 이유,
matched 여부와 첫 matched_term 인덱스를 기록합니다.
앞선 Ready/nodeName/nodeSelector 조건에서 제외되면 affinity는 평가하지 않습니다.

invalid affinity는 **node_eligibility 단계에서 FAIL**입니다. 예:
nodeSelectorTerms 누락·빈 목록·비정상 구조, expression key/operator 누락,
미지원 operator, values 구조/개수 오류, 숫자 threshold 파싱 오류.
실제 필드 위치와 값이 evidence에 남고 gpu_feasibility도 FAIL로 연결됩니다.
기존 Static `check FILE`에는 affinity 판정을 추가하지 않았습니다.

미지원:

- preferredDuringSchedulingIgnoredDuringExecution: 평가하지 않으며 ranking에 반영하지 않습니다.
- matchFields: presence를 확인하면 미지원 FAIL로 처리합니다.
- podAffinity, podAntiAffinity, topologySpreadConstraints: 평가하지 않습니다.
- 기존 미지원 ResourceQuota, preemption, multi-replica, free GPU 계산, MIG/DRA/sharing도 그대로입니다.
- 전체 label key/value 문법 및 Kubernetes 전체 스키마 검증은 아직 수행하지 않습니다.

### 8단계 검증 기록

작업 전 전체 435개 테스트가 통과했습니다. 구현 후 전체 509개 테스트가 통과했습니다.
OR/AND, 6개 operator, NotIn key 미존재, 숫자 실패,
selector/nodeName 결합 및 GPU 후보 제한은 Fake/Mock으로 검증했습니다.
affinity mismatch GPU 8 + affinity match GPU 1 / request 2는
node_eligibility와 gpu_feasibility 모두 FAIL입니다.
해당 구현 단계 당시 PC에서 `workload-check examples/good/affinity_in_match.yaml`을 실제 실행했고
kubectl 부재로 Live/Eligibility/Feasibility FAIL, 종료 코드 1을 확인했습니다.
**해당 구현 단계 당시 환경에서는 Live 성공 경로를 검증하지 못함.** kubectl/Kubernetes 설치·설정은 수행하지 않았습니다.

## 설계 원칙 및 향후 범위

- PASS/WARN/FAIL은 deterministic rule로 결정하고 LLM은 판정을 결정하지 않습니다.
- 모든 FAIL에는 실제 evidence를 출력하고 각 기능에 pytest 테스트를 작성합니다.
- CLI는 Docker/WSL 설정을 변경하거나 CUDA workload를 실행하지 않습니다. Host/Docker GPU 검증은 별도 명령의 실제 출력으로 evidence에 보존합니다.
- CLI는 Kubernetes가 광고한 allocatable capacity를 비교하며 NVIDIA driver health, runtime GPU 사용량, 실시간 free GPU 수량을 직접 검사하지 않습니다.
- 추가 범위 후보는 matchFields 지원 또는 실행 중인 Pod 요청을 반영한 GPU 잔여량 계산입니다.

## CI Validation

`.github/workflows/ci.yml`은 Pull Request 및 main branch push 시
ubuntu-latest / Python 3.12에서 실행하도록 구성했습니다.
**CI에서 deterministic validation 및 Fake/Mock 기반 scheduling tests 자동 검증**을 수행하는 workflow입니다.

자동 검증 대상:

- 프로젝트와 개발 의존성 설치: `python -m pip install -e ".[dev]"`
- 전체 pytest (unit 및 Fake/Mock tests)
- Static good example: `examples/good/probes_complete.yaml`, 종료 코드 0
- Static bad example: `examples/bad/invalid_probe_handler.yaml`, 기대 종료 코드 1

good example은 shell의 오류 처리로 nonzero이면 step을 실패시킵니다.
bad example은 반환 코드를 저장하고 정확히 1인지 검사합니다.
따라서 의도한 FAIL은 CI 성공 조건이며, 예상 밖의 0·2 등은 CI 실패입니다.
`continue-on-error`로 오류를 숨기지 않습니다.

자동 검증하지 않는 대상:

- 실제 Kubernetes API 연결
- 실제 GPU Node와 NVIDIA Device Plugin 동작
- 실제 workload scheduling 성공
- cluster-check/workload-check의 Live 성공 경로

Evidence는 GitHub Actions run의 로그 및 `validation-evidence` artifact로 남기도록 구성했습니다.
artifact에는 pytest JUnit XML, Static CLI JSON 및 기대/실제 종료 코드 기록을 포함하며 14일 보관합니다.
artifact 업로드는 실패한 run에도 시도합니다. 증거 파일이 생성되기 전에 실패하면 일부 파일이 없을 수 있습니다.
Artifact 동작은 [GitHub 공식 upload-artifact 문서](https://github.com/actions/upload-artifact/tree/v4)를 참고했습니다.

GitHub `origin/main` push와 실제 CI 실행을 확인했습니다.
최종 감사 직전 기준 commit `aabf50cdc477c25788b790ebfbe87f6f0e9c6521`의
[CI Validation run 36882281894](https://github.com/eunsu7997/gpu-deploy-guard/actions/runs/36882281894)는 SUCCESS입니다.
이 결과는 위 자동 검증 범위만 증명하며 Live Kubernetes 또는 실제 GPU scheduling 결과를 대신하지 않습니다.

`.gitignore`는 가상 환경, Python cache, coverage/빌드/CI 출력, IDE·OS 임시 파일,
로컬 환경·인증 파일을 제외합니다. src/tests/examples/README/pyproject/.github는 Git 관리 대상입니다.

### 9단계 로컬 검증 기록

작업 전 `python -m pytest`: 509개 통과.
작업 후 `python -m pytest --junitxml=artifacts/pytest.xml`: 509개 통과, 실패·skip 없음.
workflow YAML 구조를 로컬에서 확인하고 두 Static Bash step을 Git Bash에서 실제 실행했습니다.
probes_complete.yaml은 6개 check PASS와 exit 0,
invalid_probe_handler.yaml은 resource 3개 PASS·probe 3개 FAIL과 기대 exit 1을 확인했습니다.
두 CI shell step 자체는 모두 성공(exit 0)했습니다.
로컬 evidence는 artifacts/에 저장하며 Git에서는 제외합니다. 이는 GitHub Ubuntu runner 실행 검증이 아닙니다.

## Live Kubernetes Validation

2026-10-01, baseline commit `03131cea55fed88a7c7316be25bf3359236eda43`에서
Fake/Mock 주입 없이 실제 CLI의 `KubectlRunner`와 로컬 kind Kubernetes API를 연결해 검증했습니다.
앞선 단계의 "Live 미검증"/"kubectl 부재" 기록은 당시 개발 환경의 기록이며,
이번 검증으로 아래 CPU-only Live 조회 범위를 추가 확인했습니다.

환경: Windows / Docker Server 29.8.0 / Python 3.14.7 / kubectl v1.36.1 /
kind v0.33.0 / Kubernetes Node v1.37.0.
실제 context는 `kind-gpu-guard-lab`, Node는 `gpu-guard-lab-control-plane` 1개이며 Ready입니다.
이 kind Node는 `nvidia.com/gpu` allocatable 키가 없는 **CPU-only Kubernetes 검증 환경**입니다.
호스트의 물리 GPU 유무를 뜻하지 않습니다.

### VERIFIED

- 실제 kubectl executable 인식 및 current context 조회
- 실제 Kubernetes API 연결 및 Ready Node 조회 (1/1 Ready)
- 실제 Node snapshot 파싱: 이름, Ready, labels, taints, unschedulable 및 GPU 키 누락 처리
- GPU allocatable이 없는 실제 Node에 대해 GPU 2개 요청 workload의 capacity 부족 판정

| 실제 cluster-check 항목 | 결과 |
|---|---|
| kubectl_availability | PASS |
| current_context | PASS — kind-gpu-guard-lab |
| api_connection | PASS — kubectl get nodes succeeded |
| node_ready | PASS — nodes.total=1, nodes.ready=1 |
| gpu_allocatable | WARN — nvidia.com/gpu 키 누락, 0으로 해석 |
| nvidia_device_plugin | WARN — kube-system Pod 8개 중 이름/label 기준 미발견 |

`cluster-check` 종료 코드는 **0**입니다. PASS/WARN만 있으면 0인 CLI 계약이며 GPU 준비 완료를 의미하지 않습니다.

`workload-check examples/good/workload_two_gpu.yaml`은 Static 6개 check PASS,
실제 Live 연결/Ready 조회 PASS, GPU/Device Plugin WARN을 반환했습니다.
`node_eligibility`와 `gpu_feasibility`는 **FAIL**, 종료 코드는 **1**입니다.
실제 evidence는 `GPU capacity insufficient: allocatable=0, required=2`입니다.
이는 조회 기반 사전 판정이며, workload를 apply하거나 scheduler의 Pending Event를 재현한 결과가 아닙니다.

### NOT VERIFIED

- real NVIDIA GPU allocatable on Kubernetes Node
- NVIDIA Device Plugin in a real GPU cluster (현재 검사는 이름/label 기반 존재 조회)
- real Kubernetes GPU scheduling
- vLLM GPU Pod

### Evidence 및 재현

- [environment.txt](evidence/live-cluster/environment.txt): 버전, baseline commit, 실행 환경
- [kubectl-context.txt](evidence/live-cluster/kubectl-context.txt): 실제 context 출력
- [kubectl-nodes.txt](evidence/live-cluster/kubectl-nodes.txt): 실제 wide/JSON Node 출력
- [cluster-check.json](evidence/live-cluster/cluster-check.json): 실제 CLI stdout 전체
- [workload-check.json](evidence/live-cluster/workload-check.json): 실제 CLI stdout 전체
- [command-results.txt](evidence/live-cluster/command-results.txt): 명령 및 실제 종료 코드
- [pytest.txt](evidence/live-cluster/pytest.txt): 변경 후 회귀 테스트 출력 및 종료 코드

생성 시 실행한 명령은 `kind create cluster --name gpu-guard-lab`이며,
실제 사용된 node image는 `kindest/node:v1.37.0`입니다.
기존 클러스터에서 아래 조회를 재실행할 때 current context가 맞는지 먼저 확인합니다.

```powershell
kubectl config current-context
kubectl get nodes -o wide
kubectl get nodes -o json
.\.venv\Scripts\python.exe -m gpu_guard cluster-check
.\.venv\Scripts\python.exe -m gpu_guard workload-check examples/good/workload_two_gpu.yaml
.\.venv\Scripts\python.exe -m pytest
```

kubeconfig 전체, token, certificate, private key 및 Secret은 evidence에 저장하지 않았습니다.


## GPU Boundary Validation

2026-10-01, Windows + Docker Desktop + WSL2 환경에서 Host GPU가 일반 Docker
컨테이너까지 전달되는 경로와 현재 kind Kubernetes Node의 GPU resource 노출 여부를
분리해 검증했습니다. 목적은 Docker GPU 성공을 Kubernetes GPU 성공으로 확대 해석하지 않고,
GPUDeploy Guard가 실제 Live Kubernetes snapshot에서 GPU capacity 부족을 판정하는지 확인하는 것입니다.

| 경로/검사 | 실제 결과 | 판정 범위 |
|---|---|---|
| Host | `nvidia-smi`가 NVIDIA GeForce RTX 4060을 인식, exit 0 | Host GPU 인식 VERIFIED |
| Docker GPU | CUDA 12.4.1 컨테이너의 `nvidia-smi`가 RTX 4060을 인식, exit 0 | Docker GPU access VERIFIED |
| Docker CUDA compute | NVIDIA CUDA vectorAdd sample이 50,000개 원소 연산 후 `Test PASSED`, exit 0 | Docker GPU compute VERIFIED |
| kind Node | `gpu-guard-lab-control-plane` Ready=True, capacity/allocatable에 `nvidia.com/gpu` 키 없음 | Kubernetes API/Ready 조회만 VERIFIED |
| `cluster-check` | kubectl/context/API/Ready PASS, GPU allocatable 및 Device Plugin WARN, exit 0 | GPU 미노출 상태 탐지 VERIFIED |
| `workload-check` | Static checks와 Live 조회 PASS, node eligibility와 GPU feasibility FAIL, exit 1 | 요청 2 GPU 대비 allocatable 0 판정 VERIFIED |

Docker GPU access와 CUDA compute 성공은 Kubernetes에서 GPU workload가 배치됐다는 증거가 아닙니다.
현재 kind Node의 `status.capacity`와 `status.allocatable`에는 모두
`nvidia.com/gpu`가 없습니다. `cluster-check`의 exit 0은 PASS/WARN 조합에 대한
CLI 계약이며, Kubernetes GPU 준비 완료를 뜻하지 않습니다.

### VERIFIED

- Host RTX 4060 인식
- 일반 Docker CUDA 컨테이너의 GPU access
- Docker 컨테이너의 실제 CUDA vectorAdd 연산
- 실제 kind Kubernetes API 연결 및 Ready Node 조회
- GPUDeploy Guard의 Kubernetes GPU capacity 누락 탐지
- GPU 2개 요청 workload에 대한 node eligibility 및 GPU feasibility FAIL 판정

### NOT VERIFIED

- Kubernetes Node의 실제 `nvidia.com/gpu` capacity/allocatable
- 실제 GPU Node에서 NVIDIA Device Plugin 성공
- 실제 Kubernetes GPU scheduling
- GPU Pod 실행

### Evidence

- [host-nvidia-smi.txt](evidence/gpu-boundary/host-nvidia-smi.txt): Host `nvidia-smi` 원문과 exit code
- [docker-gpu.txt](evidence/gpu-boundary/docker-gpu.txt): Docker CUDA 컨테이너 GPU 조회
- [docker-gpu-compute.txt](evidence/gpu-boundary/docker-gpu-compute.txt): NVIDIA CUDA vectorAdd 실제 연산
- [kubernetes-node-gpu.txt](evidence/gpu-boundary/kubernetes-node-gpu.txt): 실제 Node JSON과 GPU key 확인
- [cluster-check.json](evidence/gpu-boundary/cluster-check.json): 실제 `cluster-check` JSON 출력
- [workload-check.json](evidence/gpu-boundary/workload-check.json): 실제 `workload-check` JSON 출력
- [summary.txt](evidence/gpu-boundary/summary.txt): 검증 범위, exit code, VERIFIED/NOT VERIFIED 요약

kubeconfig 전체, token, certificate, private key 및 Secret은 evidence에 저장하지 않았습니다.

## Preflight vs Actual Scheduler

GPUDeploy Guard의 배포 전 판정과 실제 Kubernetes scheduler 결과가 같은 원인을
가리키는지 확인하기 위해 GPU 2개를 요청하는
`examples/good/workload_two_gpu.yaml`을 기존 CPU-only kind cluster에 적용했습니다.

| 단계 | 실제 결과 |
|---|---|
| 사전판정 | `workload-check`의 `node_eligibility`와 `gpu_feasibility`가 FAIL, exit 1 |
| 예측 원인 | `GPU capacity insufficient: allocatable=0, required=2` |
| 적용 | `kubectl apply` 성공, Deployment `two-gpu-workload` 생성 |
| Pod 상태 | `Pending`, Node 미할당, Deployment Ready `0/1` |
| Scheduler Event | `FailedScheduling`: `0/1 nodes are available: 1 Insufficient nvidia.com/gpu.` |
| 비교 | GPU capacity 부족이라는 같은 원인을 지목하므로 **MATCH** |

`kubectl apply` 성공은 workload 실행 성공을 의미하지 않습니다.
Deployment object는 생성됐지만 Pod는 Pending이며 컨테이너 실행 증거가 없습니다.
실제 scheduling 실패 원인은 scheduler Event에 기록된
`Insufficient nvidia.com/gpu`입니다. GPUDeploy Guard의 사전판정과 실제 scheduler는
모두 이 workload를 수용할 NVIDIA GPU capacity가 없음을 원인으로 지목했습니다.

### Evidence

- [preflight.txt](evidence/deployment-compare/preflight.txt): 배포 전 `workload-check` 출력과 exit code
- [apply.txt](evidence/deployment-compare/apply.txt): 최초 `kubectl apply` 결과
- [pods.txt](evidence/deployment-compare/pods.txt): Deployment와 Pending Pod 상태
- [pod-describe.txt](evidence/deployment-compare/pod-describe.txt): Pod 상세 정보와 Events
- [scheduler-events.txt](evidence/deployment-compare/scheduler-events.txt): `FailedScheduling` Event 원문
- [summary.txt](evidence/deployment-compare/summary.txt): PRE-DEPLOY / POST-DEPLOY / MATCH 비교

이 검증은 GPU Pod가 실행됐거나 Kubernetes에서 GPU workload가 성공적으로 배치됐음을 증명하지 않습니다.

# products 서버 매뉴얼

products 서버를 운영하는 사람을 위한 문서입니다. 서버는 지금 **cupdev0**(`cupsoft@192.168.0.150`)이고, 모든 작업은
특별히 적지 않으면 **cupsoft 계정**으로 합니다. client 쪽은 [client 매뉴얼](manual-client.md)을 보세요.

> 이 문서의 명령은 구현과 샌드박스 시험을 마친 것이지만, 실제 서버에는 아직 적용하지 않았습니다.

## 목차

1. [서버가 하는 일](#1-서버가-하는-일)
2. [디렉토리 구조](#2-디렉토리-구조)
3. [도구 설치, 업그레이드, 되돌리기](#3-도구-설치-업그레이드-되돌리기)
4. [시스템 패키지](#4-시스템-패키지)
5. [새 버전을 release하기](#5-새-버전을-release하기)
6. [기존 vlist 등록 (처음 한 번)](#6-기존-vlist-등록-처음-한-번)
7. [고정 modulefile 고치기 (revision)](#7-고정-modulefile-고치기-revision)
8. [잘못 release했을 때: 철회](#8-잘못-release했을-때-철회)
9. [점검](#9-점검)
10. [하지 말아야 할 것](#10-하지-말아야-할-것)
11. [문제 해결](#11-문제-해결)

---

## 1. 서버가 하는 일

- product를 **빌드해서 최종 경로**(`/home/cupsoft/Products/<NAME>/<VERSION>`)에 설치합니다. 빌드는 사람이 합니다.
- 버전들을 묶어 **vlist로 release**합니다(`prod release`). release하면 그 버전과 vlist는 잠기고(`a-w`) 다시 바꿀 수
  없습니다.
- client들은 서버에서 **읽기만** 합니다(`prod sync`가 rsync로 받아 감). 서버가 client에 무엇을 밀어 넣지는 않습니다.

용어:

| 용어 | 뜻 |
|---|---|
| vlist | product 버전 묶음의 이름(`3.1`, `daq_2.1`). `Products/vlist/<name>.yml` |
| staging | 버전 디렉토리는 있지만 아직 release되지 않은 상태 (manifest가 없음). 마음대로 고치거나 지워도 됨 |
| frozen | release되어 manifest가 생긴 버전. 잠겨 있고 고치지 않음 |
| manifest | 버전 디렉토리의 파일 목록과 sha256. `<NAME>/<VERSION>.manifest` |
| 고정 modulefile | release할 때 만들어 고정한 버전별 modulefile. `modulefiles/<NAME>/<VERSION>` |

## 2. 디렉토리 구조

```
/home/cupsoft/Products/
├── .prod/
│   ├── current -> versions/v0.1      지금 쓰는 도구
│   ├── versions/v0.1/                도구 (읽기 전용)
│   ├── etc/prod.conf                   설정 (role: server)
│   └── state/                          history.jsonl, packages.yml(시스템 패키지 적용 기록), lock
├── vlist/
│   ├── drafts/3.2.yml                  release 전 초안 (사람이 씀)
│   ├── 3.1.yml                         release된 vlist (잠김)
│   ├── 3.1.vlist                       기존 형식. 기존 체계를 쓰는 노드를 위해 전환이 끝날 때까지 둠
│   └── 3.0.withdrawn                   철회 기록 (있을 때만)
├── modulefiles/
│   ├── ROOT/V6.32.20_3.11.14           고정 modulefile (잠김)
│   └── vlist/3.1                       vlist 묶음 modulefile (잠김)
└── ROOT/
    ├── V6.32.20_3.11.14/               바이너리 (release 후 잠김)
    ├── V6.32.20_3.11.14.manifest
    ├── V6.32.20_3.11.14.buildinfo      빌드 정보 기록 (사람이 읽는 것)
    └── setup_ROOT.sh                   기존 체계 파일. 전환이 끝날 때까지 둠
```

`prod`가 관리하지 않는 파일(`CERNLIB.tgz`, 미등록 버전 등)은 `prod list`의 "not managed by prod"에 나옵니다. `prod`는
이것들을 지우지 않습니다. 안에 버전 디렉터리(`V…`)가 하나도 없는 디렉터리(예: pyenv checkout인 `PYENV/`)는 항목별로
나열하지 않고 `PYENV/  (whole directory, no versions in it)` 한 줄로 나옵니다.

## 3. 도구 설치, 업그레이드, 되돌리기

### 3.1 처음 설치

```bash
git clone https://github.com/cup-software2018/prodmanager.git ~/Devs/prodmanager   # 위치는 아무 곳이나
cd ~/Devs/prodmanager
git checkout v0.1                                   # 노드에는 tag만 설치하는 것이 원칙
./install.sh --role server
```

install.sh가 하는 일:

- `/home/cupsoft/Products/.prod/versions/v0.1/`에 도구를 복사하고 `current`를 바꿉니다.
- `.prod/etc/prod.conf`가 없을 때만 만듭니다(`role: server`).
- `~/.local/bin/prod`를 symlink로 만듭니다.
- **서버에서는 기존 `~/setup_prod.sh`를 건드리지 않고, `~/setup_prod.csh`도 만들지 않습니다**(업그레이드할 때도
  마찬가지). 기존 `install_products.sh`가 노드에 설치할 때 서버의 `~/setup_prod*`를 복사해 가기 때문입니다.
  [3.4절](#34-전환-기간-기존-방식과-함께-쓰기)을 보세요.
- 끝에 `prod doctor --quick`을 실행해 보여 줍니다. 문제가 있어도 설치 자체는 성공입니다.

필요한 시스템 패키지(`environment-modules`, `rsync`, `openssh-clients`, `python3`, `python3-pyyaml`, `git`)가 없으면
install.sh가 멈추고 설치할 것을 알려 줍니다. [4장](#4-시스템-패키지)을 먼저 하세요.

### 3.2 업그레이드

```bash
cd ~/Devs/prodmanager && git fetch --tags && git checkout v0.2
./install.sh
prod --version                                        # prod v0.2
```

`prod.conf`와 `state/`는 그대로 남습니다. 도구 버전은 최근 3개만 남깁니다(`--keep N`으로 바꿈).

**서버에서 도구를 업그레이드하면** 새 modulefile 원본과 레시피가 다음 release부터 쓰입니다. 이미 release한 vlist의
고정 modulefile은 바뀌지 않습니다.

### 3.3 되돌리기

```bash
ln -sfn versions/v0.1 /home/cupsoft/Products/.prod/current.tmp
mv -T /home/cupsoft/Products/.prod/current.tmp /home/cupsoft/Products/.prod/current
prod --version
```

또는 이전 tag를 checkout하고 `./install.sh --force`.

### 3.4 전환 기간: 기존 방식과 함께 쓰기

기존 방식(`install_products.sh`로 설치하고 `~/setup_prod.sh <vlist>`로 쓰는 노드)이 남아 있는 동안 서버는 두 방식을
함께 제공합니다. 새 체계는 기존 파일을 지우거나 고치지 않습니다.

| 기존 방식이 쓰는 것 | 새 체계에서 |
|---|---|
| 서버의 `~/setup_prod.sh` (`install_products.sh`가 `~/setup_prod*`를 복사해 감) | 그대로 둠. install.sh는 서버에서 이 파일을 바꾸지 않고, `setup_prod`로 시작하는 파일을 새로 만들지도 않음 |
| `Products/vlist/<name>.vlist` | 그대로 둠. 새 체계는 옆에 `<name>.yml`을 만듦 |
| `Products/<NAME>/setup_<NAME>.sh` | 그대로 둠 |
| `Products/<NAME>/<VERSION>/` (tar로 복사해 감) | 내용은 그대로. 등록(release)하면 **잠김**(읽기 전용). 읽기만 하는 tar 복사는 계속 동작 |

전환 기간에 서버에서 새 방식을 쓰려면 전체 경로로 source합니다.

```bash
. /home/cupsoft/Products/.prod/current/share/setup_prod.sh 3.1
```

**잠금에 따라 달라지는 점** (등록한 버전에만 해당):

- 등록한 버전 디렉토리에는 아무도 쓸 수 없습니다. 예를 들어 **`pip install`로 PYTHON V3.11.14에 패키지를 더할 수
  없습니다.** 사용자는 사용자별 설치(pip가 자동으로 `~/.local`에 설치)나 `venv --system-site-packages`를 씁니다
  ([client 매뉴얼 3.1절](manual-client.md#31-python-패키지-추가-pip)). 모두가 써야 하는 패키지는 그 패키지를 넣은 새
  PYTHON 버전을 만들어 새 vlist로 release합니다.
- `install_products.sh`로 복사해 간 노드에서도 복사본이 읽기 전용입니다(tar가 권한을 그대로 옮김).

모든 노드를 새 체계로 옮긴 뒤 서버의 `~/setup_prod.sh`도 새 것으로 바꿉니다.

```bash
cd ~/Devs/prodmanager && ./install.sh --replace-legacy-setup     # 기존 것은 ~/setup_prod.sh.pre-prod로 보관
```

## 4. 시스템 패키지

패키지 목록은 저장소의 `packages/*.yml`(그룹: `base`, `root`, `geant4`, `python`, `daq`, `caen`)에 있고, 도구와 함께
설치됩니다. **root로** 실행합니다.

```bash
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --check   # 무엇이 설치될지만 봄
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh           # 설치
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --group root --group daq   # 일부 그룹만
```

- crb와 epel 저장소를 켭니다.
- 그룹마다 한 번에 설치하고, 실패하면 패키지를 하나씩 다시 시도해 **실패한 패키지만** 보고합니다.
- `optional` 패키지(R 관련)는 실패해도 경고만 합니다. `R-Rcpp-devel`, `R-RInside-devel`은 지금 저장소에 없습니다.
- `dnf update`는 하지 않습니다.
- 로그: `/var/log/prodmanager-postinstall.log`. 적용 기록: `Products/.prod/state/packages.yml`.
- 필수 패키지가 하나라도 실패하면 종료 코드 1.

확인: `prod doctor` (cupsoft로)가 빠진 패키지와 적용 기록을 보여 줍니다.

## 5. 새 버전을 release하기

예: ROOT V6.36.06_3.11.14와 Geant4 V11.4.2로 새 vlist `3.2`를 만든다.

### 5.1 빌드 (staging)

경로를 옮길 수 없으므로 **처음부터 최종 경로에** 설치합니다.

```bash
cmake -DCMAKE_INSTALL_PREFIX=/home/cupsoft/Products/ROOT/V6.36.06_3.11.14 ...
make install
```

release 전까지는 이 디렉토리를 마음대로 고치거나 지워도 됩니다(staging). client는 받지 않습니다.

### 5.2 저장소에 레시피 추가

release하려면 그 버전의 **레시피**가 도구에 들어 있어야 합니다. 저장소에 추가합니다.

```yaml
# recipes/ROOT/V6.36.06_3.11.14.yml
format: "1"
product: "ROOT"
version: "V6.36.06_3.11.14"
depends:
  - {name: "PYTHON", version: "V3.11.14"}      # 이 버전을 쓰려면 함께 load해야 하는 product
source: {url: "https://root.cern/download/root_v6.36.06.source.tar.gz"}
build: |
  cmake -DCMAKE_INSTALL_PREFIX=... -Dpython3=ON ...
notes: "자유롭게 적는 메모"
```

- 도구가 검사하는 것은 `format`, `product`, `version`, `depends`뿐입니다. `source`, `build`, `notes`는 기록입니다.
  `build`가 있으면 buildinfo에 "recipe로 빌드"라고 적힙니다.
- **새 product**(지금 없는 이름)라면 modulefile 원본도 만들어야 합니다([5.6절](#56-새-product의-modulefile-원본)).
  빌드 정보를 남기려면 `recipes/<NAME>/buildinfo.sh`도 만듭니다.

커밋하고 tag를 붙인 뒤 서버의 도구를 업그레이드합니다([3장](#3-도구-설치-업그레이드-되돌리기)).

### 5.3 초안 쓰기

```bash
mkdir -p /home/cupsoft/Products/vlist/drafts      # 처음 한 번
```

```yaml
# /home/cupsoft/Products/vlist/drafts/3.2.yml
format: "1"
description: "PYTHON 3.11 + ROOT 6.36 + Geant4 11.4"
products:                          # 순서 = load 순서. 의존 대상이 먼저 와야 함
  - {name: "PYTHON", version: "V3.11.14"}
  - {name: "ROOT", version: "V6.36.06_3.11.14"}
  - {name: "HDF5", version: "V1.14.2"}
  - {name: "GEANT4", version: "V11.4.2"}
```

- vlist 이름은 파일 이름에서 정해지고, `[A-Za-z0-9._-]`만 씁니다. **한 번 쓴 이름은 다시 쓰지 않습니다**(철회된 이름,
  기존 `.vlist`의 이름 포함).
- 권장 관례: `[<용도>_]<major>.<minor>` (`3.2`, `daq_2.2`). Python이나 ROOT 세대가 바뀌면 major를 올립니다.

### 5.4 검사, 그리고 release

```bash
prod release 3.2 --check     # 아무것도 쓰지 않고 검사만 (manifest 계산 포함, 큰 버전은 수십 초)
prod release 3.2
```

출력 예:

```
release 3.2: 4 products
  PYTHON           V3.11.14                 frozen, verified                   module PYTHON/V3.11.14
  ROOT             V6.36.06_3.11.14         staging -> frozen, 7612 entries    module ROOT/V6.36.06_3.11.14 (new)
  HDF5             V1.14.2                  frozen, verified                   module HDF5/V1.14.2
  GEANT4           V11.4.2                  staging -> frozen, 61234 entries   module GEANT4/V11.4.2 (new)
vlist hash sha256:…
```

release가 하는 일:

1. 이미 frozen인 버전은 manifest와 대조해 바뀌지 않았는지 확인합니다.
2. staging인 버전은 검사(절대경로/끊어진 symlink, 특수 파일 등)하고 manifest와 buildinfo를 만듭니다.
3. 버전마다 고정 modulefile을 만들고(이미 있으면 그대로), vlist 안에서 의존성이 맞는지 확인합니다.
4. 버전 디렉토리를 잠그고, manifest, modulefile, 묶음 modulefile, **마지막에 vlist**를 씁니다. 초안은 지워집니다.

문제는 한 번에 모아서 보고합니다. 고친 뒤 다시 실행하면 됩니다.

진행 상황은 stderr에 버전마다 나옵니다(`staging: hashing every file ...`, `frozen: verifying … entries ...` 등). 위의
요약은 stdout입니다. `prod -q release …`는 둘 다 끕니다.

`--check`의 `warning: ... suspicious file ...`(편집기 백업 `*~`, `*.orig`, 압축 파일, ELF core dump)은 release 전에 반드시
확인하세요. release하면 그 파일도 버전의 일부로 고정됩니다. 패키지가 원래 함께 설치하는 파일(예: Python의
`lib/python3.11/test/*.zip`, JupyterLab의 `package.json.orig`)도 경고에 나올 수 있는데, 그런 것은 그대로 두면 됩니다.

### 5.5 client에 알리기

노드 관리자가 노드마다 `prod sync 3.2`를 실행합니다([client 매뉴얼 5장](manual-client.md#5-vlist-설치와-갱신)).
서버가 client에 설치해 주는 기능은 없습니다.

### 5.6 새 product의 modulefile 원본

product마다 저장소의 `modulefiles/<NAME>/template.tcl`이 있어야 합니다. `prod release`가 버전마다 이것을 채워 고정
modulefile을 만듭니다. 예(HDF5):

```tcl
#%Module
@HEADER@
module-whatis "HDF5 @VERSION@"
conflict HDF5
@DEPENDS@
setenv       HDF5SYS           @PREFIX@
prepend-path PATH              @PREFIX@/bin
prepend-path LD_LIBRARY_PATH   @PREFIX@/lib
prepend-path CMAKE_PREFIX_PATH @PREFIX@
```

규칙 (`prod release`가 검사합니다):

- `#%Module`로 시작합니다.
- 자리표시자는 `@NAME@`, `@VERSION@`, `@PREFIX@`(버전 디렉토리의 절대경로), `@DEPENDS@`, `@GENERATED@`, `@HEADER@`만
  씁니다. `@HEADER@`(출처 주석)와 `@DEPENDS@`(의존성 검사 코드)는 정확히 한 번 씁니다.
- `conflict <NAME>` 줄이 있어야 합니다. 그래서 한 product는 한 버전만 load됩니다.
- `setenv <VAR> @PREFIX@` 줄이 **정확히 하나** 있어야 합니다. 이 `<VAR>`가 그 product의 "home 변수"이고, 다른
  product가 의존성을 확인할 때 씁니다(예: ROOT는 `ROOTSYS`, PYTHON은 `PYTHON_HOME`).
- 메시지를 출력하지 않고, 시스템 경로(`/usr/lib64` 등)와 macOS용 변수는 넣지 않습니다.
- 사용자마다 다른 값은 `$::env(HOME)`으로 씁니다(load할 때 정해짐).

버전마다 다른 줄이 필요하면 실행 파일 `modulefiles/<NAME>/generate`를 두고 템플릿에 `@GENERATED@`를 한 번 씁니다.
`generate`는 버전 디렉토리를 인자로 받아 Tcl 줄을 출력하고, release할 때 한 번만 실행됩니다. product가 자기 setup
스크립트(`thisroot.sh` 같은)를 제공하면 `modulefiles/_common/sh2mod.py`로 그 스크립트가 바꾸는 환경을 캡처할 수
있습니다(ROOT, GEANT4의 `generate` 참고). 캡처한 결과는 명시적인 줄로 고정되므로, load할 때 스크립트를 실행하지
않습니다.

`buildinfo.sh`는 버전 디렉토리를 인자로 받아 `key<TAB>value` 줄을 출력하는 스크립트입니다. 결과는
`<VERSION>.buildinfo`에 기록으로 남습니다(검증에는 쓰지 않음).

## 6. 기존 vlist 등록 (처음 한 번)

기존 `vlist/<name>.vlist`를 **같은 이름으로** 새 체계에 등록합니다. 바이너리는 다시 빌드하지 않습니다. 대상은 6개입니다:
`3.0`, `3.1`, `daq_1.0`, `daq_2.0`, `daq_2.1`, `mon_1.0`. (ANACONDA가 든 `1.0`, `2.0`과 버전이 없는 `0.1`, `0.9`, `1.1`,
`2.1`은 등록하지 않습니다.)

```bash
prod release --migrate 3.0 --check      # 먼저 검사
prod release --migrate 3.0
```

- 기존 `.vlist`에서 주석(`#GEANT4 ...`)을 뺀 `NAME VERSION` 줄을 그대로 옮깁니다. 내용은 바꿀 수 없습니다.
- 기존 `.vlist`와 `<NAME>/setup_<NAME>.sh`는 **건드리지 않습니다.** 기존 체계를 쓰는 노드는 계속 동작합니다.
- 기존 버전 디렉토리는 이때 처음 잠깁니다. **`--check`의 suspicious file 경고를 꼭 확인**하세요.
- 한 번 등록하면 다시 할 수 없습니다(이미 release된 이름).

## 7. 고정 modulefile 고치기 (revision)

release한 modulefile은 고칠 수 없습니다. 잘못된 것이 있으면 저장소의 원본(`modulefiles/<NAME>/template.tcl` 또는
`generate`)을 고쳐 도구를 업그레이드한 뒤, **새 vlist**를 release하면서 revision을 만듭니다.

```bash
prod release 3.1.1 --module-rev ROOT/V6.32.20_3.11.14
#   ROOT ... module ROOT/V6.32.20_3.11.14-m2 (new)
```

- 바이너리와 manifest는 그대로이고, 새 vlist만 `ROOT/V6.32.20_3.11.14-m2`를 가리킵니다.
- 이전 vlist(`3.1`)는 이전 modulefile을 그대로 씁니다.
- `--module-rev`가 없으면 그 버전의 가장 높은 revision을 씁니다.

## 8. 잘못 release했을 때: 철회

release는 고치지 않습니다. 철회 표시를 하고 새 이름으로 다시 release합니다.

```bash
prod withdraw 3.2 --reason "Geant4 data sets incomplete" --replaced-by 3.2.1
```

- `--replaced-by`에 줄 vlist(여기서는 `3.2.1`)는 먼저 release되어 있어야 합니다. 대체할 것이 없으면 생략합니다.
- `vlist/3.2.withdrawn`이 생깁니다. vlist 파일은 그대로입니다.
- 새로 `prod sync 3.2`하는 client는 거부됩니다("use 3.2.1 instead"). `--force`로만 설치됩니다.
- 이미 설치한 client에서는 `setup_prod.sh 3.2`와 `prod verify`가 경고를 보여 줍니다.
- 철회된 vlist의 내용은 서버에서 지우지 않습니다(누가 쓰는지 서버는 모릅니다).

## 9. 점검

```bash
prod verify              # release된 모든 vlist + 어떤 vlist에도 없는 frozen 버전까지 manifest와 비교
prod verify 3.1 --quick  # 내용 해시 없이 종류, 실행 비트, 크기만 (빠름)
prod verify --fix-perms  # 쓰기 권한이 켜진 것을 다시 잠금 (내용이 다른 것은 고치지 않음)
prod list                # vlist, 버전과 쓰는 vlist, 관리 밖의 파일
prod doctor              # 도구, 시스템 패키지, module load, ldd
```

`prod verify`가 오류(`content differs` 등)를 내면 **서버의 release된 바이트가 바뀌었다는 뜻**입니다. 원인을 조사하세요.
client들은 이 상태의 버전을 받으려 하면 "the server's files differ from the manifest"로 멈춥니다.

기록: `Products/.prod/state/history.jsonl` (release, withdraw, verify, doctor, install 등이 한 줄씩).

## 10. 하지 말아야 할 것

- **release된 버전 디렉토리, manifest, modulefile, vlist를 고치거나 지우지 마세요.** 잠금(`a-w`)은 실수를 막는 장치일
  뿐이고, `chmod`로 풀 수 있습니다. 풀고 고치면 모든 client의 sync와 verify가 실패합니다.
- **release된 vlist 이름을 다시 쓰지 마세요.** 바로잡을 일은 새 이름 + 철회로 합니다.
- 서버에서 `prod sync`를 실행하지 마세요(role이 server라 거부됩니다).
- `prod`나 `install.sh`를 root로 실행하지 마세요(거부됩니다). root는 `postinstall.sh`에만 씁니다.

## 11. 문제 해결

### 종료 코드

| 코드 | 뜻 |
|---|---|
| 0 | 성공 |
| 1 | 검사에서 문제를 찾음 (release 거부, verify 실패 등) |
| 2 | 사용법 오류 |
| 3 | 환경 오류 (설정 없음, role 불일치, 서버 접속 실패) |
| 4 | 다른 `prod`가 실행 중 |
| 5 | 도구가 모르는 파일 형식 → 도구를 업그레이드 |

### 자주 보는 메시지

| 메시지 | 뜻과 대처 |
|---|---|
| `no draft .../vlist/drafts/3.2.yml; write it first` | 초안을 먼저 쓰세요 (5.3절) |
| `vlist '3.2' is already released; names are never reused` | 이름을 바꾸세요. 고칠 일이면 철회 + 새 이름 |
| `'3.0' is a legacy vlist name; use --migrate to register it` | 기존 `.vlist`의 이름입니다. 등록하려면 `--migrate` |
| `ROOT V… needs PYTHON V…, which is not in this vlist` | 레시피의 `depends`에 있는 product를 vlist에 넣으세요 |
| `… which must come before it in the list` | 의존 대상을 더 앞에 두세요 |
| `ROOT V… is frozen but differs from its manifest: …` | release된 버전이 서버에서 바뀌었습니다. 누가 왜 바꿨는지 조사하세요 |
| `no recipe .../recipes/ROOT/V….yml` | 레시피를 저장소에 추가하고 도구를 업그레이드하세요 (5.2절) |
| `no modulefile template …/template.tcl` | 새 product입니다. 템플릿을 저장소에 추가하세요 |
| `--module-rev …: there is no fixed modulefile to revise yet` | 아직 release된 적 없는 버전입니다. `--module-rev` 없이 release |
| `another prod is running on this node; try again later` | 다른 `prod`가 끝난 뒤 다시 실행 |
| `… format 2 is newer than this tool supports (1); update the tool` | 도구를 업그레이드하세요 |
| `'sync' runs only where role is 'client' (this node: 'server')` | 서버에서는 sync하지 않습니다 |
| `…/PRODUCTS is missing (installed by an older install.sh); run install.sh again` | 도구 설치본에 Products 경로 기록이 없습니다. 지금 tag에서 `./install.sh --force`로 다시 설치 |

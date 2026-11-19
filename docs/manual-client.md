# client 매뉴얼

products를 받아 쓰는 노드(client)를 위한 문서입니다. 앞부분은 **사용자**(환경 설정만 하는 사람), 뒷부분은 **노드
관리자**(cupsoft 계정으로 노드를 준비하고 products를 설치하는 사람)를 위한 것입니다. 서버 쪽은
[서버 매뉴얼](manual-server.md)을 보세요.

> 이 문서의 명령은 구현과 샌드박스 시험을 마친 것이지만, 실제 노드에는 아직 적용하지 않았습니다.

## 목차

**사용자**

1. [환경 설정](#1-환경-설정)
2. [스크립트에서 쓰기](#2-스크립트에서-쓰기)
3. [module 명령으로 직접 쓰기](#3-module-명령으로-직접-쓰기), [Python 패키지 추가 (pip)](#31-python-패키지-추가-pip)

**노드 관리자**

4. [새 노드 준비](#4-새-노드-준비)
5. [vlist 설치와 갱신](#5-vlist-설치와-갱신)
6. [점검과 복구](#6-점검과-복구)
7. [vlist 빼기와 정리](#7-vlist-빼기와-정리)
8. [도구 업그레이드와 되돌리기](#8-도구-업그레이드와-되돌리기)
9. [기존 체계에서 옮기기](#9-기존-체계에서-옮기기)
10. [문제 해결](#10-문제-해결)

---

# 사용자

## 1. 환경 설정

기존과 같습니다.

```bash
. ~/setup_prod.sh 3.1                    # cupsoft 계정
. /home/cupsoft/setup_prod.sh 3.1        # 다른 계정 (예: amore2)
source /home/cupsoft/setup_prod.csh 3.1  # csh/tcsh
```

- 인자 없이 실행하면 이 노드에 설치된 vlist 목록을 보여 줍니다.
- 다른 vlist를 source하면 **앞의 vlist가 깨끗이 빠지고** 새 vlist로 바뀝니다. 같은 것을 여러 번 source해도 PATH 등이
  중복되지 않습니다.
- 대화식 셸에서는 한 줄 요약을 보여 줍니다:

  ```
  prod: vlist 3.1: PYTHON/V3.11.14 ROOT/V6.32.20_3.11.14 HDF5/V1.14.2 GEANT4/V11.1.3 CAENHVWRAPPER/V6.6
  ```

- 철회된 vlist면 경고와 대신 쓸 vlist를 보여 줍니다(load는 합니다):

  ```
  prod: warning: vlist 3.0 was withdrawn: "HDF5 1.14.1 is too old"
  prod: warning: use "3.1" instead
  ```

- 이 노드에 없는 vlist면 `prod: vlist '3.2' is not installed on this node; run 'prod sync 3.2'`가 나옵니다. 노드
  관리자에게 설치를 부탁하세요.
- 기존처럼 `PRODUCTS_DIR`(= `/home/cupsoft/Products`)도 설정됩니다.

달라진 점: 환경은 environment-modules로 설정되므로 `module list`로 무엇이 load되었는지 볼 수 있습니다. 경로 중복이나
이전 버전의 경로가 남던 문제는 없어졌습니다.

## 2. 스크립트에서 쓰기

```bash
#!/bin/bash
set -eu
. /home/cupsoft/setup_prod.sh 3.1        # 실패하면 여기서 멈춤 (set -e)
root -b -q analysis.C
```

- 비대화식 셸에서는 **아무것도 출력하지 않습니다**(오류와 경고만).
- `set -e`, `set -u`를 켠 스크립트에서도 안전합니다. 실패하면 0이 아닌 값을 돌려줍니다.
- `module` 명령이 없는 셸(cron, ssh로 실행한 명령 등)에서도 스스로 environment-modules를 초기화합니다.

## 3. module 명령으로 직접 쓰기

```bash
module use /home/cupsoft/Products/modulefiles
module avail                             # vlist/3.1, ROOT/V6.32.20_3.11.14, ...
module load vlist/3.1                    # setup_prod.sh 3.1과 같음
module load ROOT/V6.32.20_3.11.14        # product 하나만 (필요한 PYTHON도 자동으로 load)
module switch vlist vlist/daq_2.1
module unload ROOT                       # 자동으로 load된 PYTHON도 함께 내려감
```

한 product는 한 버전만 load됩니다. 다른 버전의 PYTHON이 load된 상태에서 ROOT를 load하면 "needs PYTHON V3.11.14,
but another PYTHON is loaded"로 거부됩니다.

## 3.1 Python 패키지 추가 (pip)

products의 PYTHON은 읽기 전용이라 그 안에는 패키지를 설치할 수 없습니다. 대신 두 가지 방법이 있습니다.

```bash
. ~/setup_prod.sh 3.1
pip install iminuit                      # 자동으로 사용자 설치(~/.local/lib/python3.11/)가 됨
```

pip는 site-packages에 쓸 수 없으면 "Defaulting to user installation because normal site-packages is not writeable"이라고
알리고 **그 사용자의 홈에** 설치합니다(`pip install --user`와 같음). 그 사용자에게만 보이고, products는 바뀌지 않습니다.

작업마다 패키지를 따로 두려면 venv를 씁니다. products의 패키지(numpy, ROOT의 Python 바인딩 등)는 그대로 보입니다.

```bash
. ~/setup_prod.sh 3.1
python3 -m venv --system-site-packages ~/venvs/myana
. ~/venvs/myana/bin/activate
pip install iminuit
```

**모든 사용자나 DAQ 같은 공용 작업에 필요한 패키지**는 노드마다 설치하지 말고 서버 관리자에게 요청하세요. 그 패키지를
넣은 새 PYTHON 버전을 서버에서 release합니다.

---

# 노드 관리자

모두 **cupsoft 계정**으로 합니다(4.1의 root 작업 제외). `prod`는 노드마다 그 노드에서 직접 실행합니다. 여러 노드에
한꺼번에 설치하는 기능은 두지 않습니다.

## 4. 새 노드 준비

### 4.1 root가 할 일

1. `cupsoft` 계정을 만듭니다.
2. `/home/cupsoft/Products`를 준비합니다. 큰 로컬 디스크에 두려면 symlink로 만듭니다(경로
   `/home/cupsoft/Products`가 그대로 보이기만 하면 됩니다):

   ```bash
   mkdir -p /scratch/cupsoft/Products && chown cupsoft: /scratch/cupsoft/Products
   ln -s /scratch/cupsoft/Products /home/cupsoft/Products
   ```

3. 시스템 패키지: 도구를 설치한 뒤(4.3) `postinstall.sh`를 실행합니다. 도구 설치에 필요한 최소 패키지가 없으면 먼저
   설치합니다:

   ```bash
   dnf install environment-modules rsync openssh-clients python3 python3-pyyaml git
   ```

### 4.2 서버 접속

client는 서버에 **비밀번호 없이** ssh로 접속할 수 있어야 합니다.

```bash
ssh -o BatchMode=yes cupsoft@192.168.0.150 true && echo ok
```

안 되면 cupsoft의 ssh 키를 서버의 `~cupsoft/.ssh/authorized_keys`에 등록합니다.

### 4.3 도구 설치

```bash
git clone https://github.com/cup-software2018/prodmanager.git ~/Devs/prodmanager   # 위치는 아무 곳이나
cd ~/Devs/prodmanager && git checkout vX.Y
./install.sh --role client --server cupsoft@192.168.0.150:/home/cupsoft/Products
```

- 도구가 `/home/cupsoft/Products/.prod/`에 설치되고, `~/setup_prod.sh`, `~/setup_prod.csh`, `~/.local/bin/prod`가
  생깁니다. 기존의 실제 `~/setup_prod.sh`는 `~/setup_prod.sh.pre-prod`로 옮겨집니다.
- 설정은 `/home/cupsoft/Products/.prod/etc/prod.conf`에 있습니다. 서버를 바꿀 때만 고칩니다.
- 끝에 `prod doctor --quick` 결과가 나옵니다.

### 4.4 시스템 패키지 (root)

```bash
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh --check
sudo /home/cupsoft/Products/.prod/current/share/postinstall.sh
```

자세한 것은 [서버 매뉴얼 4장](manual-server.md#4-시스템-패키지)과 같습니다. 필수 패키지가 실패하면 종료 코드 1이고,
`prod doctor`가 빠진 것을 보여 줍니다.

## 5. vlist 설치와 갱신

```bash
prod list --remote               # 서버에 있는 vlist 목록
prod sync 3.1 daq_2.1            # 설치 (여러 개 가능)
prod sync 3.1 --dry-run          # 무엇을 받을지만 봄
prod sync --installed            # 이 노드의 모든 vlist를 서버 상태로 다시 맞춤 (복구)
```

출력 예:

```
sync 3.1 sha256:… (first sync)
  PYTHON           V3.11.14                 transferred
  ROOT             V6.32.20_3.11.14         transferred
  HDF5             V1.14.2                  in place
  GEANT4           V11.1.3                  transferred
  CAENHVWRAPPER    V6.6                     transferred
  done in 41.3 s
```

sync가 하는 일:

- 서버의 vlist를 받아, **처음 받을 때의 해시를 기록**해 둡니다. 다음부터는 서버의 vlist가 그때와 다르면 멈춥니다
  (release된 vlist는 바뀌면 안 되므로).
- 이미 맞는 버전은 건드리지 않습니다(`in place`). 다르거나 없는 버전만 받습니다. 바뀌지 않은 파일은 재사용하므로
  고칠 때 전송량이 적습니다.
- 받은 것을 sha256으로 전부 확인하고 잠근 뒤(`a-w`) 제자리에 놓습니다. 실행 중인 프로그램은 깨지지 않습니다.
- vlist 파일은 맨 마지막에 놓습니다. `setup_prod.sh`로 보이는 vlist는 필요한 것이 모두 준비된 것입니다.
- 중간에 끊겨도(네트워크 등) 다시 실행하면 이어서 받습니다.

## 6. 점검과 복구

```bash
prod verify                      # 이 노드의 모든 vlist를 manifest와 비교 (서버 접속 없음)
prod verify 3.1 --quick          # 내용 해시 없이 빠르게
prod verify --remote             # 서버의 vlist가 처음 받았을 때와 같은지도 확인
prod verify --fix-perms          # 쓰기 권한이 켜진 것을 다시 잠금
prod doctor                      # 도구, 서버 접속, 시스템 패키지, module load, 빠진 공유 라이브러리(ldd)
prod list                        # vlist, 버전과 쓰는 vlist, 관리 밖의 파일
```

`prod verify` 출력의 해시(`sha256:…`)를 노드끼리 비교하면 같은 release를 쓰는지 알 수 있습니다.
`prod --json verify`(`list`, `doctor`도 같음)는 결과를 JSON으로 출력합니다. 스크립트로 결과를 읽을 때 씁니다.

### 설치본이 망가졌을 때

```
3.1          1 problem(s)   sha256:…
  error:   ROOT V6.32.20_3.11.14: lib/libCore.so: content differs
```

```bash
prod sync --installed            # 서버 상태로 되돌림
prod verify
```

설치본은 잠겨 있으니 직접 고치지 마세요. 시험하려면 다른 곳에 복사해서 하세요.

### 서버의 vlist가 바뀌었다고 할 때

```
prod: the server's vlist '3.1' differs from the one first synced on this node
```

release된 vlist는 바뀌면 안 됩니다. **서버 관리자에게 알리세요.** 관리자가 의도한 변경이라고 확인해 주면:

```bash
prod sync --retrust 3.1          # 새 해시를 기록하고 진행 (history에 남음)
```

### 서버의 바이트가 다르다고 할 때

```
prod: ROOT V…: the server's files differ from the manifest even after a full re-transfer (…);
      the products server needs to be checked
```

서버에서 release된 파일이 바뀌었다는 뜻입니다. 이 노드에는 아무것도 바뀌지 않았습니다. 서버 관리자에게 알리세요.

### 철회된 vlist

```
prod: vlist '3.0' was withdrawn: HDF5 1.14.1 is too old; use 3.1 instead (--force installs it anyway)
```

대체 vlist를 쓰세요. 꼭 필요하면 `prod sync --force 3.0`.

## 7. vlist 빼기와 정리

```bash
prod remove 3.0                  # 이 노드에서 vlist를 뺌 (버전 디렉토리는 아직 남음)
prod gc                          # 어떤 vlist도 쓰지 않는 버전 등 지울 것 목록만 보여 줌
prod gc --yes                    # 실제로 지움
```

- `gc`는 `prod`가 관리하는 것만 지웁니다. 기존 체계의 파일(`setup_ROOT.sh`, `CERNLIB.tgz` 등)은 절대 지우지 않습니다.
- 뺀 vlist를 나중에 다시 sync하면 처음 받았을 때의 해시와 비교합니다.

## 8. 도구 업그레이드와 되돌리기

```bash
cd ~/Devs/prodmanager && git fetch --tags && git checkout vX.Y
./install.sh                     # prod.conf와 기록은 그대로 남음
prod --version
```

되돌리기:

```bash
ls /home/cupsoft/Products/.prod/versions/          # 남아 있는 버전 (최근 3개)
ln -sfn versions/v0.1 /home/cupsoft/Products/.prod/current.tmp
mv -T /home/cupsoft/Products/.prod/current.tmp /home/cupsoft/Products/.prod/current
```

도구를 업그레이드해도 이미 설치한 vlist의 환경은 바뀌지 않습니다(modulefile이 release 때 고정되므로).
`prod`가 "format N is newer than this tool supports"라고 하면 도구를 업그레이드하세요.

## 9. 기존 체계에서 옮기기

이미 `install_products.sh`로 products를 받아 쓰던 노드입니다.

1. **먼저 확인**: 이 노드에서 쓰는 vlist와 스크립트. 특히 기존 vlist 파일을 노드에서 직접 고쳐 쓰던 경우(예: cupdev1의
   `3.0`)는 새 체계에서 서버의 내용으로 바뀌므로, 그 vlist를 쓰는 스크립트를 먼저 정리해야 합니다.
2. 도구 설치(4.3)와 시스템 패키지(4.4).
3. `prod sync <쓰는 vlist들>`. 기존에 받아 둔 버전 디렉토리는 재사용되고, 서버와 다른 부분만 고쳐집니다(파이썬이
   만든 `__pycache__` 등은 지워짐).
4. `prod verify`, `prod doctor`, 그리고 실제 작업 스크립트를 한 번씩 돌려 봅니다.

되돌리기: `mv ~/setup_prod.sh.pre-prod ~/setup_prod.sh`. 기존 파일(`vlist/*.vlist`, `setup_<NAME>.sh`)은 지우지 않았으므로
기존 방식이 그대로 동작합니다.

옮긴 노드에서는 **기존 `install_products.sh`를 더 이상 실행하지 마세요.** 그 스크립트는 서버의 기존
`~/setup_prod.sh`를 받아 와 새 것을 덮어쓰려 합니다. 기존 방식을 계속 쓰려면 `--keep-legacy-setup`으로 설치해
`~/setup_prod.sh`를 기존 것으로 두고, 새 방식은 `. /home/cupsoft/Products/.prod/current/share/setup_prod.sh <vlist>`로
씁니다.

## 10. 문제 해결

### 종료 코드

| 코드 | 뜻 |
|---|---|
| 0 | 성공 |
| 1 | 검사에서 문제를 찾음 (verify 실패, 해시 불일치, 철회됨, doctor FAIL 등) |
| 2 | 사용법 오류 |
| 3 | 환경 오류 (설정 없음, role 불일치, 서버 접속 실패) |
| 4 | 다른 `prod`가 실행 중 → 끝난 뒤 다시 |
| 5 | 도구가 모르는 파일 형식 → 도구 업그레이드 |

### 자주 보는 메시지

| 메시지 | 뜻과 대처 |
|---|---|
| `prod: vlist '3.2' is not installed on this node; run 'prod sync 3.2'` | `prod sync 3.2` |
| `vlist '3.2' is not on the products server` | 이름을 확인하세요(`prod list --remote`) |
| `cannot read from the products server …` | ssh 접속 확인 (4.2) |
| `another prod is running on this node; try again later` | 다른 `prod`가 끝난 뒤 다시 |
| `the products server is inconsistent for vlist …` | 서버 쪽 문제. 서버 관리자에게 |
| `no configuration file …/prod.conf; run install.sh` | 도구가 설치되지 않았습니다 (4.3) |
| `…/PRODUCTS is missing (installed by an older install.sh); run install.sh again` | 도구 설치본에 Products 경로 기록이 없습니다. 지금 tag에서 `./install.sh --force`로 다시 설치 |
| `'release' runs only where role is 'server' (this node: 'client')` | release는 서버에서 |
| `prod: environment-modules is not installed …` | `dnf install environment-modules` (root) |
| `prod: local vlists (~/.prod-local) are not supported yet` | 로컬 vlist는 아직 구현 전입니다 |

### 아직 안 되는 것

- 사용자 로컬 vlist(`~/.prod-local`)는 아직 구현하지 않았습니다.
- `setup_prod.csh`는 csh가 있는 노드에서 아직 시험하지 못했습니다.

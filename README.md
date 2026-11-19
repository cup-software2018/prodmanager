# prodmanager

**products manager**: CUP의 products(ROOT, Geant4, Python, HDF5, CUPDAQ 등)를 products 서버 한 대에서 release하고, 모든 노드가 **같은
바이트를 같은 경로(`/home/cupsoft/Products`)에** 받아 쓰게 하는 관리도구 `prod`와 그 정의입니다.

- 이 저장소에 있는 것: 도구(`prod`, `setup_prod.sh`), modulefile 원본, 빌드 레시피, 시스템 패키지 목록
- 이 저장소에 없는 것: products 바이너리, release 정의(vlist), manifest. 이것들은 products 서버에 있습니다.

> **상태: 구현과 시험 완료, 실제 노드 적용 전.** 서버(cupdev0)부터 차례로 적용합니다.

## 문서

| 문서 | 누가 읽나 |
|---|---|
| [docs/manual-server.md](docs/manual-server.md) | products 서버 관리자: 도구 설치, 시스템 패키지, 새 버전 release, 기존 vlist 등록, 철회, 점검 |
| [docs/manual-client.md](docs/manual-client.md) | 노드 관리자와 사용자: 노드 준비, vlist 설치(sync), 환경 설정, 점검과 복구 |

## 한눈에 보기

```
 GitHub prodmanager ──git checkout <tag> + ./install.sh──▶ 각 노드의 /home/cupsoft/Products/.prod/  (도구)

 products 서버 (cupdev0)                                   client (모든 노드)
 /home/cupsoft/Products/                                   /home/cupsoft/Products/
   ROOT/V6.32.20_3.11.14/ ...  ── prod sync <vlist> ──▶      (서버와 같은 구조, 쓰는 vlist의 것만)
   vlist/3.1.yml (release 정의)    (rsync, 해시 검증)
   modulefiles/                                             . ~/setup_prod.sh 3.1   ← 사용자
```

- **vlist**: product 버전들의 묶음 이름(`3.1`, `daq_2.1`). release하면 바꿀 수 없고, 같은 이름은 어느 노드에서나 같은
  바이트를 뜻합니다.
- **사용법은 그대로**: `. ~/setup_prod.sh 3.1`. 내부에서는 environment-modules로 환경을 설정합니다.
- **언제든 확인**: `prod verify`가 설치본이 release 때와 같은지 sha256으로 확인합니다.

## 빠른 시작

**사용자**

```bash
. ~/setup_prod.sh 3.1                     # 다른 계정이면: . /home/cupsoft/setup_prod.sh 3.1
```

**노드 관리자 (cupsoft)**

```bash
git clone https://github.com/<owner>/prodmanager.git && cd prodmanager && git checkout vX.Y.Z
./install.sh --role client --server cupsoft@192.168.0.150:/home/cupsoft/Products
prod sync 3.1 daq_2.1
prod verify
```

**서버 관리자 (cupsoft@cupdev0)**: [docs/manual-server.md](docs/manual-server.md)

## 저장소 구성

| 경로 | 내용 |
|---|---|
| `install.sh` | 도구를 `<Products>/.prod`에 설치 (Products 소유자가 실행) |
| `postinstall.sh` | 시스템 패키지 설치 (root로 실행) |
| `bin/prod`, `lib/prod/` | 도구 본체 (시스템 python3 3.9 + PyYAML) |
| `shell/` | `setup_prod.sh`, `setup_prod.csh` |
| `modulefiles/<NAME>/` | modulefile 원본 (`template.tcl`, `generate`), `_common/` 공용 도우미 |
| `recipes/<NAME>/` | 버전별 레시피(`<VERSION>.yml`: 의존성 등)와 `buildinfo.sh` |
| `packages/` | 시스템 패키지 목록 (그룹별) |
| `tests/` | 단위 테스트, 샌드박스 시험, 실제 데이터 시험 |

## 시험

```bash
/usr/bin/python3 -I tests/run_unit.py                    # 단위 테스트 (수 초)
/usr/bin/python3 -I tests/sandbox/run.py                 # 끝에서 끝까지: 가짜 서버와 client, 실제 install.sh,
                                                         # prod, rsync, localhost ssh, environment-modules
/usr/bin/python3 -I tests/realdata/env_equivalence.py    # 실제 products: 기존 setup과 새 modulefile의 환경 비교
```

- 앞의 둘은 임시 디렉토리에서만 동작하고 실제 Products, 서버, home은 건드리지 않습니다. 샌드박스는 비밀번호 없는
  `ssh localhost`가 필요합니다(`--no-ssh`로 생략). 실패하면 샌드박스 디렉토리를 남겨 둡니다(`--keep`으로도).
- 셋째는 읽기만 합니다. 이 노드의 기존 vlist마다 기존 `~/setup_prod.sh <vlist>`의 환경과, 새 modulefile(임시
  디렉토리에 생성)의 환경을 비교해, 의도한 차이(버린 macOS용 변수, 고친 버그 등)로 설명되지 않는 차이를
  보고합니다.

## 개발

- 커밋, tag, push는 사람이 합니다. 노드에는 tag(`vX.Y.Z`)만 설치하는 것이 원칙입니다.
- 코드, 주석, `prod`의 메시지는 영어로, 설계 문서와 매뉴얼은 한국어로 씁니다.

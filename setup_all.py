import os
import random
import string
import subprocess
import sys
from pathlib import Path


def print_step(title):
    print(f"\n{'=' * 10} [단계] {title} {'=' * 10}")


def generate_secret_key(length=48):
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def run_cmd(command, shell=True):
    print(f"실행 중: {command}")
    result = subprocess.run(command, shell=shell)
    if result.returncode != 0:
        print(f"[오류] 명령어 실행 실패 (코드: {result.returncode})")
        sys.exit(result.returncode)


def run_mysql_file(user, password, database, file_path, ignore_duplicate_error=False):
    """MySQL 실행 파일 절대 경로를 사용하여 안전하게 SQL 파일 실행 (중복 에러 선택적 무시)"""
    if not Path(file_path).exists():
        print(f"[경고] 파일이 존재하지 않아 건너뜁니다: {file_path}")
        return

    mysql_bin = r'"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"'

    db_arg = f" {database}" if database else ""
    pass_arg = f" -p{password}" if password else ""

    command = f"{mysql_bin} -u {user}{pass_arg}{db_arg} < {file_path}"
    print(f"SQL 실행: {file_path} (계정: {user}, DB: {database or '없음'})")

    if ignore_duplicate_error:
        # 이미 생성된 테이블/인덱스 등으로 인한 에러(1050, 1061 등)는 경고로 처리하고 진행
        result = subprocess.run(command, shell=True)
        if result.returncode != 0:
            print(f"[안내] 이미 존재하는 객체이거나 무시 가능한 오류 코드({result.returncode})입니다. 계속 진행합니다.")
    else:
        run_cmd(command)


def main():
    print("🚀 프로젝트 전체 사전작업 및 초기화 통합 스크립트를 시작합니다.")

    # 0. 필수 패키지 설치
    print_step("0. 필수 패키지 설치 (mysql-connector-python)")
    run_cmd(f"{sys.executable} -m pip install mysql-connector-python")

    # 1. 비밀키 생성 및 .env 파일 확인/작성
    print_step("1. 환경 변수(.env) 설정 및 비밀키 생성")
    env_path = Path("../.env")
    secret_key = generate_secret_key(48)

    if not env_path.exists():
        print(f"'.env' 파일이 없어 기본 템플릿을 생성합니다.")
        env_content = f"""# 로그인 계정 DB 설정
ACCOUNT_DB_HOST=127.0.0.1
ACCOUNT_DB_PORT=3306
ACCOUNT_DB_NAME=account_db
ACCOUNT_DB_USER=root
ACCOUNT_DB_PASSWORD=
AUTH_SECRET_KEY={secret_key}
AUTH_ACCESS_TOKEN_EXPIRE_MINUTES=60
AUTH_COOKIE_SECURE=false
ACCOUNT_SEED_ADMIN_PASSWORD=admin123!
ACCOUNT_SEED_HR_PASSWORD=hr123!
ACCOUNT_SEED_FINANCE_PASSWORD=finance123!

# 사내규정 챗봇(RAG) 설정
DOCUMENT_DB_HOST=127.0.0.1
DOCUMENT_DB_USER=root
DOCUMENT_DB_PASSWORD=
DOCUMENT_DB_DATABASE=erp_system
FAISS_PATH=data/faiss
EMBEDDING_BACKEND=sbert

# 판매(Sales) 도메인 설정
SALES_DB_HOST=127.0.0.1
SALES_DB_USER=JangGGo
SALES_DB_PASSWORD=
SALES_DB_DATABASE=sales
SALES_READ_USER=sales_reader
SALES_READ_PASSWORD=reader123!

# 구매(Purchase) 도메인 설정
PURCHASE_DB_HOST=127.0.0.1
PURCHASE_DB_USER=purchase
PURCHASE_DB_PASSWORD=1234
PURCHASE_DB_DATABASE=purchase
PURCHASE_READ_USER=purchase_reader
PURCHASE_READ_PASSWORD=purchase_read_1234
"""
        env_path.write_text(env_content, encoding="utf-8")
        print(".env 파일 생성 완료.")
    else:
        print("기존 .env 파일을 유지합니다.")

    # 사용자로부터 MySQL root 비밀번호 입력받기
    root_password = input(
        "MySQL root 계정 비밀번호를 입력하세요 (비밀번호가 없으면 엔터): "
    ).strip()

    # 2. 로그인 계정용 DB 및 구조 생성 (이미 존재할 수 있으므로 중복 무시 옵션 적용 가능)
    print_step("2. 로그인 계정 DB 구조 생성 (Account)")
    run_mysql_file("root", root_password, "", "database/account/001_create_account_db.sql", ignore_duplicate_error=True)
    run_mysql_file("root", root_password, "account_db", "database/account/002_create_accounts_table.sql",
                   ignore_duplicate_error=True)
    run_mysql_file("root", root_password, "account_db", "database/account/003_create_account_views.sql",
                   ignore_duplicate_error=True)

    # 3. 사내규정 챗봇 DB 및 RAG 파이프라인 실행
    print_step("3. 사내규정 챗봇(RAG) DB 및 문서 인덱싱")
    run_mysql_file("root", root_password, "", "database/document/schema.sql", ignore_duplicate_error=True)

    if Path("scripts/register_documents.py").exists():
        run_cmd(f"{sys.executable} scripts/register_documents.py")
    if Path("scripts/ingest_documents.py").exists():
        run_cmd(f"{sys.executable} scripts/ingest_documents.py")

    # 4. 판매(Sales) 도메인 세팅 및 ETL 실행
    print_step("4. 판매(Sales) 도메인 세팅 및 ETL 실행")
    run_mysql_file("root", root_password, "", "database/sales/create_sales_db.sql", ignore_duplicate_error=True)
    run_mysql_file("JangGGo", "", "sales", "database/sales/ddl.sql", ignore_duplicate_error=True)

    excel_path = "../data/raw/source_data/ERP_Sales_Data_Full_5y.xlsx"
    if Path(excel_path).exists():
        run_cmd(f"{sys.executable} -m etl.sales.run_all {excel_path}")
    else:
        print(f"[건너뜀] 엑셀 파일이 없습니다: {excel_path}")

    run_mysql_file("JangGGo", "", "sales", "database/sales/views.sql", ignore_duplicate_error=True)
    run_mysql_file("root", root_password, "sales", "database/sales/grants_reader.sql", ignore_duplicate_error=True)

    # 5. 구매(Purchase) 도메인 세팅 및 ETL 실행
    print_step("5. 구매(Purchase) 도메인 세팅 및 ETL 실행")
    run_mysql_file("root", root_password, "", "database/purchase/create_purchase_db.sql", ignore_duplicate_error=True)

    purchase_etl_path = "etl/purchase/main.py"
    if Path(purchase_etl_path).exists():
        # 스크립트 경로로 직접 실행하면 프로젝트 루트가 sys.path에 잡히지 않아
        # main.py의 `from etl.purchase...` 절대 임포트가 'No module named etl'로
        # 실패한다. sales ETL과 동일하게 -m 모듈 실행으로 맞춘다.
        run_cmd(f"{sys.executable} -m etl.purchase.main")
    else:
        print(f"[건너뜀] 구매 ETL 파일이 없습니다: {purchase_etl_path}")

    run_mysql_file("JangGGo", "1234", "purchase", "database/purchase/views.sql", ignore_duplicate_error=True)
    run_mysql_file("JangGGo", "1234", "purchase", "database/purchase/grants_reader.sql", ignore_duplicate_error=True)

    print("\n✨ 모든 사전작업 및 초기화 코드가 성공적으로 실행되었습니다!")


if __name__ == "__main__":
    main()
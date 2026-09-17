@echo off
rem Resumable pull of the remaining daily files: sec_bhavdata_full (Oct 2019 on), MTO (to mid-2020), then
rem the PR zips that carry NSE's band-hit lists; then the full parquet build and the D4 simulation (about
rem 70 minutes). Safe to rerun: files already downloaded are skipped. Never run while another
rem `impact fetch` is running. --no-sync leaves impact.exe alone while other jobs run.
cd /d "%~dp0.."
uv run --no-sync impact fetch daily --source sec_full || exit /b 1
uv run --no-sync impact fetch daily --source mto || exit /b 1
uv run --no-sync impact fetch daily --source pr || exit /b 1
uv run --no-sync impact build || exit /b 1
uv run --no-sync impact simulate || exit /b 1
echo Daily pull, build and simulation complete.

@echo off
rem Stop the website and DELETE its database: every account, claimed venue and
rem listed event. up.bat starts again from a clean copy of the Ticketmaster data.
rem   down.bat      asks first
rem   down.bat /y   no question
cd /d "%~dp0"
if /i not "%~1"=="/y" (
    choice /m "Stop the site and delete ALL accounts and events in concert_tickets"
    if errorlevel 2 (
        echo Nothing changed.
        exit /b 1
    )
)
docker compose down || exit /b 1
rem A one-off container with the same settings and network runs the reset.
docker compose run --rm --no-deps -T web python reset_db.py --yes || exit /b 1
echo Site stopped and database deleted.

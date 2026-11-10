@echo off
rem Start the website in Docker. On a fresh database it loads the Ticketmaster
rem data and creates the demo logins. Start learn-databases (SQL Server) first.
cd /d "%~dp0"
docker compose up -d --build || exit /b 1
echo.
echo Starting... (first start loads the data, about 10 seconds)
echo   Site:     http://127.0.0.1:5000
echo   Venue:    demo.venue@example.com / DemoVenue2026!
echo   Customer: demo.customer@example.com / DemoCustomer2026!
echo   Logs:     docker compose logs -f web

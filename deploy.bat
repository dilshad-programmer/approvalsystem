@echo off
REM ============================================================
REM  Cloud Document Approval System - Manual Deployment Script
REM
REM  Run this script from inside the "doc approve" folder
REM  using fresh AWS Academy credentials in your .env file.
REM
REM  Usage: deploy.bat
REM ============================================================

echo ===================================================
echo  Deploying to AWS Elastic Beanstalk
echo  Environment: Doc-approval-env-env
echo ===================================================

REM --- Step 1: Package the application ---
echo.
echo [1/4] Creating deployment package...
if exist app.zip del app.zip
powershell Compress-Archive -Path * -DestinationPath app.zip -Force
echo      Done.

REM --- Step 2: Upload to your S3 bucket ---
echo.
echo [2/4] Uploading to S3...
aws s3 cp app.zip s3://doc-approval-bucket/eb-deployments/app-latest.zip
if %errorlevel% neq 0 (
    echo ERROR: S3 upload failed. Please refresh your AWS credentials in .env
    exit /b 1
)
echo      Done.

REM --- Step 3: Create a new EB application version ---
echo.
echo [3/4] Creating Elastic Beanstalk application version...
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set VERSION=v-%datetime:~0,12%

aws elasticbeanstalk create-application-version ^
    --application-name doc-approval-app ^
    --version-label %VERSION% ^
    --source-bundle S3Bucket=doc-approval-bucket,S3Key=eb-deployments/app-latest.zip ^
    --description "Manual deployment %datetime%"
if %errorlevel% neq 0 (
    echo ERROR: Failed to create application version.
    exit /b 1
)
echo      Done. Version: %VERSION%

REM --- Step 4: Deploy the new version ---
echo.
echo [4/4] Deploying to environment...
aws elasticbeanstalk update-environment ^
    --environment-name Doc-approval-env-env ^
    --version-label %VERSION%
if %errorlevel% neq 0 (
    echo ERROR: Failed to trigger deployment.
    exit /b 1
)

echo.
echo ===================================================
echo  Deployment triggered successfully!
echo  Monitor progress in the AWS Console:
echo  https://console.aws.amazon.com/elasticbeanstalk
echo ===================================================

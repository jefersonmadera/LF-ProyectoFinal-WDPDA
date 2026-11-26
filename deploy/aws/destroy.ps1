# Elimina TODO lo que creó deploy.ps1 (para no consumir créditos).
# Vacía el bucket del frontend y borra la pila: EC2, S3, DynamoDB
# (con los casos cargados), el rol IAM y el grupo de seguridad.
param(
    [string]$Stack = "wdpda",
    [string]$Region = "us-east-1"
)
$ErrorActionPreference = "Stop"
$bucket = aws cloudformation describe-stacks --stack-name $Stack --region $Region `
    --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text
if ($bucket -and $bucket -ne "None") {
    aws s3 rm "s3://$bucket" --recursive --region $Region
}
aws cloudformation delete-stack --stack-name $Stack --region $Region
aws cloudformation wait stack-delete-complete --stack-name $Stack --region $Region
Write-Host "Pila '$Stack' eliminada."

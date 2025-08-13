docker build --no-cache  -t proxy-validator:latest .
docker tag proxy-validator:latest razor29/proxy-validator:latest
docker tag proxy-validator:latest razor29/proxy-validator:v1.0.0
docker push razor29/proxy-validator:latest
docker push razor29/proxy-validator:v1.0.0

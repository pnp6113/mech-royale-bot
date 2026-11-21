FROM golang:1.26.1-bookworm@sha256:ab3d6955bbc813a0f3fdf220c1d817dd89c0b3f283777db8ece4a32fe7858edd AS build
WORKDIR /src
ENV CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOTELEMETRY=off
COPY go.mod ./
COPY mech ./mech
COPY cmd/bot ./cmd/bot
RUN go build -mod=readonly -trimpath -buildvcs=false -ldflags="-s -w -buildid=" -o /mechbot ./cmd/bot
RUN mkdir -p /runtime/tmp
RUN chmod 1777 /runtime/tmp

FROM scratch
ENV HOME=/tmp TMPDIR=/tmp GOMAXPROCS=1
COPY --from=build /mechbot /mechbot
COPY --from=build /runtime/tmp /tmp
COPY LICENSE THIRD_PARTY_LICENSES /
USER 65534:65534
ENTRYPOINT ["/mechbot"]

FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -o /provisioner .

FROM docker:27-cli
COPY --from=build /provisioner /provisioner
ENTRYPOINT ["/provisioner"] 
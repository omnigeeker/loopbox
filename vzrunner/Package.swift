// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "vzrunner",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "vzrunner", path: "Sources/vzrunner")
    ]
)

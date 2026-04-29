import Foundation

enum Config {
    /// Override for production. For dev, point at your laptop's LAN IP, e.g. http://192.168.1.42:8000
    static let apiBase: URL = {
        if let env = ProcessInfo.processInfo.environment["API_BASE"], let u = URL(string: env) {
            return u
        }
        return URL(string: "http://localhost:8000")!
    }()

    /// V1 single-user. Will pull from auth in V3.
    static let userId = "trey"

    /// Lead side — left for right-handed golfers, right for left-handed.
    static let leadSide: LeadSide = .left

    /// Capture defaults.
    static let preferredResolution = (width: 3840, height: 2160)
    static let preferredFPS: Int32 = 60
}

enum LeadSide: String, Codable {
    case left = "L"
    case right = "R"
}

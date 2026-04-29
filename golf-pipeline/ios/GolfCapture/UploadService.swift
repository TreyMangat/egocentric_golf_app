import Foundation

/// Backend + S3 calls for the capture app.
final class UploadService {
    static let shared = UploadService()
    private init() {}

    private let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 60
        cfg.timeoutIntervalForResource = 60 * 30  // 30 minutes for big uploads
        return URLSession(configuration: cfg)
    }()

    private let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        return e
    }()

    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()

    enum UploadError: LocalizedError {
        case badStatus(Int, String)
        case noResponse
        var errorDescription: String? {
            switch self {
            case .badStatus(let code, let body):
                return "HTTP \(code): \(body.prefix(200))"
            case .noResponse: return "No response"
            }
        }
    }

    // MARK: - Backend calls

    func startSession(_ body: StartSessionRequest) async throws {
        let url = Config.apiBase.appendingPathComponent("/api/v1/sessions")
        try await postJSON(url: url, body: body, decoded: EmptyResponse.self)
    }

    func presign(_ body: PresignRequest) async throws -> PresignResponse {
        let url = Config.apiBase.appendingPathComponent("/api/v1/upload/presign")
        return try await postJSON(url: url, body: body, decoded: PresignResponse.self)
    }

    func finalize(sessionId: String, request: FinalizeRequest) async throws {
        let url = Config.apiBase.appendingPathComponent("/api/v1/sessions/\(sessionId)/finalize")
        try await postJSON(url: url, body: request, decoded: EmptyResponse.self)
    }

    // MARK: - S3 PUT

    func upload(localFile: URL, to presignedURL: String) async throws {
        guard let url = URL(string: presignedURL) else {
            throw UploadError.badStatus(0, "bad presigned url")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("video/quicktime", forHTTPHeaderField: "Content-Type")

        // Use upload(fromFile:) so iOS streams the file rather than loading
        // hundreds of MB into RAM.
        let (_, response) = try await session.upload(for: request, fromFile: localFile)
        guard let http = response as? HTTPURLResponse else { throw UploadError.noResponse }
        if !(200..<300).contains(http.statusCode) {
            throw UploadError.badStatus(http.statusCode, "S3 upload rejected")
        }
    }

    // MARK: - helpers

    private struct EmptyResponse: Decodable {}

    @discardableResult
    private func postJSON<Body: Encodable, R: Decodable>(
        url: URL, body: Body, decoded: R.Type
    ) async throws -> R {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw UploadError.noResponse }
        if !(200..<300).contains(http.statusCode) {
            let bodyText = String(data: data, encoding: .utf8) ?? "<binary>"
            throw UploadError.badStatus(http.statusCode, bodyText)
        }
        if R.self == EmptyResponse.self {
            return EmptyResponse() as! R
        }
        return try decoder.decode(R.self, from: data)
    }
}

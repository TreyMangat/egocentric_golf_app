import Foundation

// Mirrors the backend schemas in golf_pipeline/schemas.py.

enum View: String, Codable, CaseIterable, Identifiable {
    case dtl = "DTL"
    case fo = "FO"
    var id: String { rawValue }
    var label: String {
        switch self {
        case .dtl: return "Down the line"
        case .fo: return "Face on"
        }
    }
}

enum Club: String, Codable, CaseIterable, Identifiable {
    case driver = "driver"
    case threeW = "3w"
    case fiveW = "5w"
    case hybrid = "hybrid"
    case threeI = "3i"
    case fourI = "4i"
    case fiveI = "5i"
    case sixI = "6i"
    case sevenI = "7i"
    case eightI = "8i"
    case nineI = "9i"
    case pw = "pw"
    case gw = "gw"
    case sw = "sw"
    case lw = "lw"
    case putter = "putter"

    var id: String { rawValue }
    var displayName: String { rawValue.uppercased() }
}

enum Outcome: String, Codable, CaseIterable, Identifiable {
    case good, ok, bad
    var id: String { rawValue }
}

enum Shape: String, Codable, CaseIterable, Identifiable {
    case straight, draw, fade, hook, slice, fat, thin
    var id: String { rawValue }
}

/// A timestamped tag event the user creates during a session.
/// Backend associates these with the closest detected impact.
struct TagEvent: Codable, Identifiable {
    let id: UUID
    let tMs: Int                  // ms since session start
    let club: Club?
    let view: View?
    let outcome: Outcome?
    let shape: Shape?

    init(
        tMs: Int,
        club: Club? = nil,
        view: View? = nil,
        outcome: Outcome? = nil,
        shape: Shape? = nil
    ) {
        self.id = UUID()
        self.tMs = tMs
        self.club = club
        self.view = view
        self.outcome = outcome
        self.shape = shape
    }
}

// MARK: - API request/response

struct PresignRequest: Codable {
    let userId: String
    let sessionId: String
    let clipId: String
    let contentType: String

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case sessionId = "session_id"
        case clipId = "clip_id"
        case contentType = "content_type"
    }
}

struct PresignResponse: Codable {
    let uploadUrl: String
    let s3Key: String

    enum CodingKeys: String, CodingKey {
        case uploadUrl = "upload_url"
        case s3Key = "s3_key"
    }
}

struct StartSessionRequest: Codable {
    let userId: String
    let sessionId: String
    let startedAt: Date
    let location: String?
    let notes: String?

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case sessionId = "session_id"
        case startedAt = "started_at"
        case location, notes
    }
}

struct FinalizeRequest: Codable {
    let userId: String
    let captureMetadata: CaptureMetadata

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case captureMetadata = "capture_metadata"
    }
}

struct CaptureMetadata: Codable {
    let location: String?
    let phoneModel: String
    let fps: Int
    let width: Int
    let height: Int
    let leadSide: String
    let tagEvents: [TagEventDTO]
}

/// Flattened DTO so JSONEncoder produces the shape backend expects.
struct TagEventDTO: Codable {
    let tMs: Int
    let club: String?
    let view: String?
    let outcome: String?
    let shape: String?
}

extension TagEvent {
    var dto: TagEventDTO {
        TagEventDTO(
            tMs: tMs,
            club: club?.rawValue,
            view: view?.rawValue,
            outcome: outcome?.rawValue,
            shape: shape?.rawValue
        )
    }
}

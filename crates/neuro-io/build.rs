fn main() {
    #[cfg(feature = "proto")]
    {
        let proto_root = std::path::PathBuf::from("../../proto");
        let proto_file = proto_root.join("neuroplc.proto");
        println!("cargo:rerun-if-changed={}", proto_file.display());

        prost_build::Config::new()
            .compile_protos(&[proto_file], &[proto_root])
            .expect("Failed to compile protobuf definitions");
    }
}

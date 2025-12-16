plugins {
    id("java")
    id("org.jetbrains.intellij") version "1.17.4" // 可以根据本地最新版本稍微调整
    kotlin("jvm") version "1.9.23"
}

group = "com.example"
version = "0.0.1"

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.json:json:20250517")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}

intellij {
    // 指定你本机有的 IntelliJ 版本（或使用 IC-2024.1 等）
    version.set("2024.1")
    type.set("IC") // Community Edition
    plugins.set(listOf("java"))
}

tasks {
    patchPluginXml {
        sinceBuild.set("241")          // 对应 2024.1
        untilBuild.set("243.*")        // 兼容范围可自行调整
    }

    compileKotlin {
        kotlinOptions.jvmTarget = "17"
    }

    compileJava {
        targetCompatibility = "17"
        sourceCompatibility = "17"
    }

    runIde {
        // 这里可以保持默认
    }
}
